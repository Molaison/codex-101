import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import magpie_support as magpie


def completed(arguments, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(arguments, returncode, stdout, stderr)


class TemplateTests(unittest.TestCase):
    def test_template_uses_sentinel_and_explicit_contract(self):
        seen = {}
        temporary = tempfile.TemporaryDirectory(prefix="magpie-support-template-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        original = root / "magpie"
        original.write_bytes(b"fixture-magpie")

        def fake_run(arguments, env, timeout=0):
            seen["arguments"] = list(arguments)
            seen["binary"] = Path(arguments[0])
            seen["binary_exists"] = seen["binary"].is_file()
            seen["root"] = root
            provider_path = Path(env["XDG_CONFIG_HOME"]) / "magpie" / "providers.json"
            provider_path.parent.mkdir(parents=True, exist_ok=True)
            provider_path.write_text(
                json.dumps(
                    {
                        "providers": [
                            {
                                "id": "cpr",
                                "name": "CPR",
                                "key": magpie.SENTINEL_KEY,
                                "chat": magpie.CPR_BASE_URL,
                                "responses": magpie.CPR_BASE_URL,
                                "models": ["raw-a"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            return completed(arguments)

        with mock.patch.object(magpie, "_run", side_effect=fake_run):
            template = magpie._template_provider(original, ["raw-a"])

        arguments = seen["arguments"]
        self.assertNotEqual(seen["binary"], original.resolve())
        self.assertNotEqual(seen["binary"].parent, original.parent)
        self.assertTrue(seen["binary_exists"])
        self.assertIn("key=" + magpie.SENTINEL_KEY, arguments)
        self.assertIn("url=" + magpie.CPR_BASE_URL, arguments)
        self.assertIn("responses=" + magpie.CPR_BASE_URL, arguments)
        self.assertIn("models=raw-a", arguments)
        self.assertNotIn("REAL-SECRET", " ".join(arguments))
        self.assertEqual(template["models"], ["raw-a"])


class MergeTests(unittest.TestCase):
    def test_merge_preserves_unrelated_and_secures_file(self):
        with tempfile.TemporaryDirectory(prefix="magpie-support-merge-") as temporary:
            path = Path(temporary) / "magpie" / "providers.json"
            path.parent.mkdir(mode=0o700, parents=True)
            path.write_text(
                json.dumps(
                    {
                        "providers": [{"id": "other", "name": "Other", "key": "old"}],
                        "order": ["other"],
                        "unknown": {"kept": True},
                    }
                ),
                encoding="utf-8",
            )
            template = {
                "id": "cpr",
                "name": "CPR",
                "key": magpie.SENTINEL_KEY,
                "chat": magpie.CPR_BASE_URL,
                "responses": magpie.CPR_BASE_URL,
                "models": ["raw-a"],
            }
            document, changed = magpie._merge_provider(template, "REAL-SENTINEL", path)
            self.assertTrue(changed)
            magpie._atomic_write_json(path, document)
            loaded = json.loads(path.read_text(encoding="utf-8"))
            by_id = {item["id"]: item for item in loaded["providers"]}
            self.assertEqual(by_id["other"]["key"], "old")
            self.assertEqual(by_id["cpr"]["key"], "REAL-SENTINEL")
            self.assertEqual(loaded["order"], ["other"])
            self.assertEqual(loaded["unknown"], {"kept": True})
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_atomic_write_without_fchmod_keeps_private_mode(self):
        with tempfile.TemporaryDirectory(prefix="magpie-support-no-fchmod-") as temporary:
            path = Path(temporary) / "magpie" / "providers.json"
            fallback_chmod = os.chmod
            with mock.patch.object(magpie.os, "fchmod", None, create=True):
                with mock.patch.object(
                    magpie.os, "chmod", wraps=fallback_chmod
                ) as chmod:
                    magpie._atomic_write_json(
                        path, {"providers": [{"id": "other", "key": "secret"}]}
                    )
            chmod.assert_called_once_with(mock.ANY, 0o600)
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_merge_rejects_cpr_bound_elsewhere(self):
        with tempfile.TemporaryDirectory(prefix="magpie-support-reject-") as temporary:
            path = Path(temporary) / "providers.json"
            path.write_text(
                json.dumps(
                    {
                        "providers": [
                            {
                                "id": "cpr",
                                "name": "Foreign CPR",
                                "key": "old",
                                "chat": "https://different.example/v1",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            template = {
                "id": "cpr",
                "name": "CPR",
                "chat": magpie.CPR_BASE_URL,
                "responses": magpie.CPR_BASE_URL,
                "models": ["raw-a"],
            }
            with self.assertRaises(magpie.MagpieSupportError):
                magpie._merge_provider(template, "real", path)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        # These fixtures exercise the ordinary gateway, independently of HOME's mount.
        node_mode = mock.patch.object(magpie, "_node_mode", return_value=False)
        node_mode.start()
        self.addCleanup(node_mode.stop)

    def test_runtime_uses_member_paths_and_fixed_gateway(self):
        with tempfile.TemporaryDirectory(prefix="magpie-support-runtime-") as temporary:
            root = Path(temporary)
            env = {
                "HOME": str(root / "home"),
                "USERPROFILE": str(root / "home"),
                "XDG_CONFIG_HOME": str(root / "xdg"),
                "XDG_CACHE_HOME": str(root / "cache"),
                "CODEX_HOME": str(root / "codex"),
            }
            with mock.patch.dict(os.environ, env, clear=False):
                layout = magpie._runtime_layout()
                process_env = magpie._environment(layout, magpie.GATEWAY_URL, block_remote=False)
            self.assertEqual(layout["xdg_config_home"], root / "xdg")
            self.assertEqual(layout["codex_home"], root / "codex")
            self.assertEqual(process_env["MAGPIE_ADDR"], "127.0.0.1:3425")
            self.assertEqual(magpie.GATEWAY_URL, "http://127.0.0.1:3425/v1")

    def test_healthy_gateway_is_not_installed_or_restarted(self):
        calls = []
        with mock.patch.object(magpie, "_gateway_healthy", return_value=True):
            with mock.patch.object(magpie, "_run", side_effect=lambda *a, **k: calls.append(a)):
                result = magpie.ensure_daemon(Path("/fake/magpie"), {}, magpie.GATEWAY_URL)
        self.assertTrue(result["gateway_healthy"])
        self.assertEqual(result["action"], "none")
        self.assertEqual(calls, [])

    def test_healthcheck_uses_official_command(self):
        calls = []

        def fake_run(arguments, env, timeout=0):
            calls.append(arguments)
            return completed(arguments)

        with mock.patch.object(magpie, "_run", side_effect=fake_run):
            self.assertTrue(magpie._gateway_healthy(Path("/fake/magpie"), {"MAGPIE_ADDR": "127.0.0.1:3425"}))
        self.assertEqual(calls, [[str(Path("/fake/magpie")), "healthcheck"]])

    def test_stopped_daemon_is_started_not_restarted(self):
        commands = []
        health = iter([False, False, True])

        def fake_run(arguments, env, timeout=0):
            commands.append(arguments)
            if arguments[-1] == "status":
                return completed(arguments, stdout="the magpie service is not running")
            return completed(arguments)

        with mock.patch.object(magpie, "_gateway_healthy", side_effect=lambda *a: next(health)):
            with mock.patch.object(magpie, "_run", side_effect=fake_run):
                with mock.patch.object(magpie.time, "sleep"):
                    result = magpie.ensure_daemon(Path("/fake/magpie"), {}, magpie.GATEWAY_URL)
        self.assertEqual(result["action"], "start")
        self.assertTrue(any(command[-1] == "start" for command in commands))
        self.assertFalse(any(command[-1] in ("restart", "install") for command in commands))

    def test_prepare_success_requires_healthy_gateway(self):
        with tempfile.TemporaryDirectory(prefix="magpie-support-prepare-") as temporary:
            root = Path(temporary)
            paths = {
                "home": root,
                "xdg_config_home": root / "xdg",
                "xdg_cache_home": root / "cache",
                "codex_home": root / "codex",
                "bin": root / "bin",
                "log_dir": root / "cache" / "magpie",
            }
            template = {
                "id": "cpr",
                "name": "CPR",
                "key": magpie.SENTINEL_KEY,
                "chat": magpie.CPR_BASE_URL,
                "responses": magpie.CPR_BASE_URL,
                "models": ["raw-a"],
            }
            calls = []

            def fake_run(arguments, env, timeout=0):
                calls.append(arguments)
                return completed(arguments)

            with mock.patch.object(magpie, "_runtime_layout", return_value=paths):
                with mock.patch.object(magpie, "ensure_magpie", return_value=Path("/fake/magpie")):
                    with mock.patch.object(magpie, "_version", return_value="magpie test"):
                        with mock.patch.object(magpie, "_gateway_healthy", return_value=True):
                            with mock.patch.object(magpie, "_template_provider", return_value=template):
                                with mock.patch.object(magpie, "_run", side_effect=fake_run):
                                    with mock.patch.object(magpie, "_validate_codex_config"):
                                        result = magpie.prepare("REAL-SECRET", ["raw-a"], "raw-a")
            self.assertEqual(result["qualified_model"], "cpr/raw-a")
            self.assertNotIn("REAL-SECRET", json.dumps(result))
            self.assertFalse(any("REAL-SECRET" in " ".join(command) for command in calls))
            provider = json.loads(Path(result["provider_file"]).read_text(encoding="utf-8"))
            self.assertEqual(provider["providers"][0]["key"], "REAL-SECRET")

    def test_prepare_fails_before_write_when_gateway_unhealthy(self):
        with tempfile.TemporaryDirectory(prefix="magpie-support-unhealthy-") as temporary:
            root = Path(temporary)
            paths = {
                "home": root,
                "xdg_config_home": root / "xdg",
                "xdg_cache_home": root / "cache",
                "codex_home": root / "codex",
                "bin": root / "bin",
                "log_dir": root / "cache" / "magpie",
            }
            with mock.patch.object(magpie, "_runtime_layout", return_value=paths):
                with mock.patch.object(magpie, "ensure_magpie", return_value=Path("/fake/magpie")):
                    with mock.patch.object(magpie, "_version", return_value="magpie test"):
                        with mock.patch.object(magpie, "_gateway_healthy", return_value=False):
                            with mock.patch.object(
                                magpie,
                                "_ensure_gateway",
                                return_value={"gateway_healthy": False, "action": "none"},
                            ):
                                with mock.patch.object(magpie, "_template_provider") as template:
                                    with self.assertRaises(magpie.MagpieSupportError):
                                        magpie.prepare("REAL-SECRET", ["raw-a"], "raw-a")
            template.assert_not_called()
            self.assertFalse((root / "xdg" / "magpie" / "providers.json").exists())

    def _prepare_paths(self, root):
        return {
            "home": root,
            "xdg_config_home": root / "xdg",
            "xdg_cache_home": root / "cache",
            "codex_home": root / "codex",
            "bin": root / "bin",
            "log_dir": root / "cache" / "magpie",
        }

    def _template(self):
        return {
            "id": "cpr",
            "name": "CPR",
            "key": magpie.SENTINEL_KEY,
            "chat": magpie.CPR_BASE_URL,
            "responses": magpie.CPR_BASE_URL,
            "models": ["raw-a"],
        }

    def test_prepare_restores_original_config_when_validation_fails(self):
        with tempfile.TemporaryDirectory(prefix="magpie-support-rollback-") as temporary:
            root = Path(temporary)
            paths = self._prepare_paths(root)
            paths["codex_home"].mkdir()
            config = paths["codex_home"] / "config.toml"
            original = b'# original config\nmodel = "gpt-5"\n'
            config.write_bytes(original)
            providers = paths["xdg_config_home"] / "magpie" / "providers.json"
            providers.parent.mkdir(parents=True)
            providers.write_text(
                json.dumps({"providers": [{"id": "other", "key": "OTHER-SECRET"}]}),
                encoding="utf-8",
            )

            def fake_run(arguments, env, timeout=0):
                if arguments[1:2] == ["codex"]:
                    config.write_bytes(b'# magpie output\nmodel = "cpr/raw-a"\n')
                return completed(arguments)

            with mock.patch.object(magpie, "_runtime_layout", return_value=paths):
                with mock.patch.object(magpie, "ensure_magpie", return_value=Path("/fixture/magpie")):
                    with mock.patch.object(magpie, "_version", return_value="magpie test"):
                        with mock.patch.object(magpie, "_gateway_healthy", return_value=True):
                            with mock.patch.object(magpie, "_template_provider", return_value=self._template()):
                                with mock.patch.object(magpie, "_run", side_effect=fake_run):
                                    with mock.patch.object(
                                        magpie,
                                        "_validate_codex_config",
                                        side_effect=magpie.MagpieSupportError("fixture validation failed"),
                                    ):
                                        with self.assertRaises(magpie.MagpieSupportError) as raised:
                                            magpie.prepare("REAL-SECRET", ["raw-a"], "raw-a")
            self.assertNotIn("REAL-SECRET", str(raised.exception))
            self.assertEqual(config.read_bytes(), original)
            saved = json.loads(providers.read_text(encoding="utf-8"))["providers"]
            self.assertEqual({item["id"]: item["key"] for item in saved}["other"], "OTHER-SECRET")

    def test_prepare_preserves_concurrent_config_edit_on_validation_failure(self):
        with tempfile.TemporaryDirectory(prefix="magpie-support-concurrent-") as temporary:
            root = Path(temporary)
            paths = self._prepare_paths(root)
            paths["codex_home"].mkdir()
            config = paths["codex_home"] / "config.toml"
            config.write_bytes(b"original\n")
            edited = b"user edit after magpie\n"

            def fake_run(arguments, env, timeout=0):
                if arguments[1:2] == ["codex"]:
                    config.write_bytes(b"magpie post image\n")
                return completed(arguments)

            def fail_after_edit(*args, **kwargs):
                config.write_bytes(edited)
                raise magpie.MagpieSupportError("fixture validation failed")

            with mock.patch.object(magpie, "_runtime_layout", return_value=paths):
                with mock.patch.object(magpie, "ensure_magpie", return_value=Path("/fixture/magpie")):
                    with mock.patch.object(magpie, "_version", return_value="magpie test"):
                        with mock.patch.object(magpie, "_gateway_healthy", return_value=True):
                            with mock.patch.object(magpie, "_template_provider", return_value=self._template()):
                                with mock.patch.object(magpie, "_run", side_effect=fake_run):
                                    with mock.patch.object(
                                        magpie,
                                        "_validate_codex_config",
                                        side_effect=fail_after_edit,
                                    ):
                                        with self.assertRaises(magpie.MagpieSupportError):
                                            magpie.prepare("REAL-SECRET", ["raw-a"], "raw-a")
            self.assertEqual(config.read_bytes(), edited)

    def test_prepare_rejects_portable_runtime_before_real_writes(self):
        for marker in (".portable", "data"):
            with self.subTest(marker=marker):
                with tempfile.TemporaryDirectory(prefix="magpie-support-portable-") as temporary:
                    root = Path(temporary)
                    binary_root = root / "runtime"
                    binary = binary_root / "magpie"
                    binary.parent.mkdir()
                    binary.write_bytes(b"fixture")
                    marker_path = binary_root / marker
                    if marker == "data":
                        marker_path.mkdir()
                    else:
                        marker_path.write_text("portable\n", encoding="utf-8")
                    paths = self._prepare_paths(root)
                    config = paths["codex_home"] / "config.toml"
                    paths["codex_home"].mkdir()
                    config.write_bytes(b"original\n")

                    with mock.patch.object(magpie, "_runtime_layout", return_value=paths):
                        with mock.patch.object(magpie, "ensure_magpie", return_value=binary):
                            with mock.patch.object(magpie, "_run") as run:
                                with self.assertRaisesRegex(
                                    magpie.MagpieSupportError,
                                    "unsupported portable Magpie runtime",
                                ):
                                    magpie.prepare("REAL-SECRET", ["raw-a"], "raw-a")
                    run.assert_not_called()
                    self.assertEqual(config.read_bytes(), b"original\n")
                    self.assertFalse(
                        (paths["xdg_config_home"] / "magpie" / "providers.json").exists()
                    )


@unittest.skipUnless(os.name == "posix" and Path("/tmp").is_dir(), "Unix node-local fixtures")
class PerNodeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="magpie-node-fixture-", dir="/tmp")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.binary = self.root / "fixture-magpie"
        self.provider = {
            "id": "cpr", "name": "CPR", "key": magpie.SENTINEL_KEY,
            "chat": magpie.CPR_BASE_URL, "responses": magpie.CPR_BASE_URL,
            "models": ["raw-a", "raw-b"],
        }
        for target in ("_run", "subprocess.run", "urllib.request.urlopen", "os.execve"):
            patcher = mock.patch.object(magpie, target, create=False) if "." not in target else mock.patch(
                "magpie_support." + target)
            guard = patcher.start()
            guard.side_effect = AssertionError("fixture must not execute processes or use the network")
            self.addCleanup(patcher.stop)
        template = mock.patch.object(magpie, "_template_provider", side_effect=lambda *args: {
            **self.provider, "models": list(self.provider["models"]),
        })
        self.template = template.start()
        self.addCleanup(template.stop)
        sockets = mock.patch.object(magpie.socket, "socket")
        self.sockets = sockets.start()
        self.addCleanup(sockets.stop)
        self.listener = self.sockets.return_value.__enter__.return_value
        self.listener.getsockname.return_value = ("127.0.0.1", 31337)

    def create_seed(self):
        return magpie._node_seed(self.root, "fixture-key", ["raw-a", "raw-b"], self.binary)

    def test_first_seed_is_published_complete_and_private_by_atomic_link(self):
        link = os.link
        observed = []

        def inspect_publication(source, destination):
            pending, target = Path(source), Path(destination)
            self.assertFalse(target.exists())
            self.assertEqual(pending.stat().st_mode & 0o777, 0o600)
            observed.append(json.loads(pending.read_text(encoding="utf-8")))
            return link(source, destination)

        with mock.patch.object(magpie.os, "link", side_effect=inspect_publication) as publish:
            seed = self.create_seed()
        publish.assert_called_once()
        self.assertEqual(observed, [seed])
        self.assertEqual(seed["gateway_url"], "http://127.0.0.1:31337/v1")
        self.assertEqual(seed["provider"]["key"], "fixture-key")
        self.assertEqual(seed["provider"]["models"], ["raw-a", "raw-b"])
        self.assertEqual((self.root / "seed.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual({item.name for item in self.root.iterdir()}, {"seed.json"})
        self.listener.bind.assert_called_once_with(("127.0.0.1", 0))

    def test_existing_seed_reuses_port_and_accepts_requested_model_subset(self):
        seed = self.create_seed()
        path = self.root / "seed.json"
        original, inode = path.read_bytes(), path.stat().st_ino
        self.template.reset_mock()
        self.sockets.reset_mock()
        reused = magpie._node_seed(self.root, "fixture-key", ["raw-b"], self.binary)
        self.assertEqual(reused, seed)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(path.stat().st_ino, inode)
        self.template.assert_not_called()
        self.sockets.assert_not_called()

    def test_existing_seed_rejects_changed_key_or_unseeded_models_without_overwrite(self):
        self.create_seed()
        path = self.root / "seed.json"
        original, inode = path.read_bytes(), path.stat().st_ino
        self.template.reset_mock()
        self.sockets.reset_mock()
        selections = (
            ("different-fixture-key", ["raw-a"]),
            ("fixture-key", ["raw-a", "raw-c"]),
            ("fixture-key", ["raw-c"]),
        )
        for key, models in selections:
            with self.subTest(models=models, changed_key=key != "fixture-key"):
                with self.assertRaises(magpie.MagpieSupportError) as raised:
                    magpie._node_seed(self.root, key, models, self.binary)
                self.assertNotIn(key, str(raised.exception))
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(path.stat().st_ino, inode)
        self.template.assert_not_called()
        self.sockets.assert_not_called()

    def test_concurrent_seed_publication_keeps_winner_instead_of_overwriting(self):
        winner = {
            "schema": 1, "gateway_url": "http://127.0.0.1:31338/v1",
            "provider": dict(self.provider, key="fixture-key"),
        }
        link = os.link
        original = []

        def competing_publication(source, destination):
            magpie._atomic_write_json(Path(destination), winner)
            original.append(Path(destination).read_bytes())
            return link(source, destination)  # Real EEXIST, after the initial absence check.

        with mock.patch.object(magpie.os, "link", side_effect=competing_publication):
            seed = self.create_seed()
        self.assertEqual(seed, winner)
        self.assertEqual((self.root / "seed.json").read_bytes(), original[0])
        self.assertEqual({item.name for item in self.root.iterdir()}, {"seed.json"})

    def test_unregistered_node_exits_without_reading_seed_or_executing(self):
        nodes = self.root / "nodes"
        nodes.mkdir()
        (nodes / "another-node.json").write_text("{}", encoding="utf-8")
        with mock.patch.object(magpie.socket, "getfqdn", return_value="unregistered.fixture"), \
                mock.patch.object(magpie, "_private_document") as read_document, \
                mock.patch.object(magpie, "_node_layout") as layout, \
                mock.patch.object(magpie.os, "execve") as execute:
            self.assertEqual(magpie._node_serve(self.root), 0)
        read_document.assert_not_called()
        layout.assert_not_called()
        execute.assert_not_called()
        self.assertFalse((self.root / "seed.json").exists())

    def test_private_tmp_directory_rejects_symlink_in_parent_chain(self):
        target = self.root / "untouched-target"
        target.mkdir(mode=0o755)
        original_mode = target.stat().st_mode & 0o777
        planted = self.root / "planted-parent"
        planted.symlink_to(target, target_is_directory=True)
        with self.assertRaises(magpie.MagpieSupportError):
            magpie._private_directory(planted / "nested" / "runtime")
        self.assertTrue(planted.is_symlink())
        self.assertEqual(target.stat().st_mode & 0o777, original_mode)
        self.assertEqual(list(target.iterdir()), [])


class PerNodeFilesystemTests(unittest.TestCase):
    def test_linux_autodetects_actual_home_mount_through_findmnt(self):
        paths = {"home": Path("/fixture/shared-home")}
        for filesystem, expected in (("lustre", True), ("ext4", False), ("nfs", False)):
            with self.subTest(filesystem=filesystem), \
                    mock.patch.dict(os.environ, {"CODEX101_MAGPIE_PER_NODE": ""}), \
                    mock.patch.object(magpie.platform, "system", return_value="Linux"), \
                    mock.patch.object(magpie.subprocess, "run", return_value=completed(
                        [], stdout=filesystem.upper() + "\n")) as findmnt:
                self.assertIs(magpie._node_mode(paths), expected)
                findmnt.assert_called_once_with(
                    ["findmnt", "-n", "-T", str(paths["home"]), "-o", "FSTYPE"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, timeout=5, check=False)

    def test_non_linux_never_probes_or_automatically_enables_node_mode(self):
        for system in ("Darwin", "Windows"):
            with self.subTest(system=system), \
                    mock.patch.dict(os.environ, {"CODEX101_MAGPIE_PER_NODE": ""}), \
                    mock.patch.object(magpie.platform, "system", return_value=system), \
                    mock.patch.object(magpie.subprocess, "run") as run:
                self.assertFalse(magpie._node_mode({"home": Path("/fixture/home")}))
                run.assert_not_called()

    def test_failed_mount_detection_fails_closed(self):
        with mock.patch.dict(os.environ, {"CODEX101_MAGPIE_PER_NODE": ""}), \
                mock.patch.object(magpie.platform, "system", return_value="Linux"), \
                mock.patch.object(magpie.subprocess, "run", return_value=completed([], returncode=1)):
            with self.assertRaises(magpie.MagpieSupportError):
                magpie._node_mode({"home": Path("/fixture/home")})


class WindowsAssetTests(unittest.TestCase):
    def _feed(self, digest="a" * 64):
        return {
            "assets": {
                "magpie-cli-windows-amd64.exe": {
                    "url": "https://example.invalid/magpie.exe",
                    "sha256": digest,
                }
            }
        }

    def test_windows_asset_uses_official_feed_name_and_hash(self):
        response = mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(json.dumps(self._feed()).encode("utf-8"))
        with mock.patch.object(magpie.platform, "machine", return_value="AMD64"):
            with mock.patch.object(magpie.urllib.request, "urlopen", return_value=response):
                url, digest, name = magpie._windows_asset()
        self.assertEqual(name, "magpie-cli-windows-amd64.exe")
        self.assertEqual(url, "https://example.invalid/magpie.exe")
        self.assertEqual(digest, "a" * 64)

    def test_windows_checksum_mismatch_is_rejected(self):
        payload = b"fixture-binary"
        digest = "0" * 64
        with tempfile.TemporaryDirectory(prefix="magpie-support-windows-") as temporary:
            downloaded = Path(temporary) / "asset.bin"
            downloaded.write_bytes(payload)
            self.assertNotEqual(magpie._sha256_file(downloaded), digest)

    def test_windows_startup_marker_protects_unrelated_file(self):
        with tempfile.TemporaryDirectory(prefix="magpie-support-startup-") as temporary:
            root = Path(temporary)
            appdata = root / "appdata"
            startup = appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
            startup.mkdir(parents=True)
            target = startup / "codex101-magpie.cmd"
            target.write_bytes(b"@echo unrelated\r\n")
            paths = {
                "home": root / "home",
                "xdg_config_home": root / "home" / ".config",
                "xdg_cache_home": root / "home" / ".cache",
                "codex_home": root / "home" / ".codex",
                "log_dir": root / "home" / ".cache" / "magpie",
            }
            with mock.patch.dict(os.environ, {"APPDATA": str(appdata)}, clear=False):
                with self.assertRaises(magpie.MagpieSupportError):
                    magpie._startup_cmd(paths, Path("C:/magpie/magpie.exe"), magpie.GATEWAY_URL)
            self.assertEqual(target.read_bytes(), b"@echo unrelated\r\n")


if __name__ == "__main__":
    unittest.main()
