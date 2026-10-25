"""Bounded offline checks for the cross-platform lab installer preset."""

import json
from pathlib import Path
import os
import tempfile
import getpass
import contextlib
import io
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

import codex_provider as c


class LabInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_catalog_only_explicit_v1(self):
        connection = {"catalog_source": str(Path(__file__).parent / "model_catalog.json")}
        with mock.patch.object(c, "state_directory", return_value=self.root):
            catalog = c.native_catalog(connection, [], v1_only=True)
        models = json.loads(catalog.read_text(encoding="utf-8"))["models"]
        self.assertTrue(models)
        self.assertTrue(all(m.get("multi_agent_version") == "v1" for m in models))
        self.assertIn(c.LAB_CPR_MODEL, {m["slug"] for m in models})
        self.assertNotIn("gpt-6.1-sol", {m["slug"] for m in models})

    def test_real_upstream_readiness_failure_is_not_misdiagnosed_as_local_network(self):
        code, hint = c.check_problem("Reconnecting... 1/12 (stream disconnected before completion: ChatGPT personalization preflight exceeded its readiness deadline)")
        self.assertEqual(code, "UPSTREAM_READINESS")
        self.assertIn("维护者", hint)

    def test_known_upstream_failure_is_not_reinstalled_or_blindly_resubmitted(self):
        directory = self.root / "checks/attempt-existing"
        directory.mkdir(parents=True)
        (directory / "state.json").write_text(json.dumps({
            "status": "terminal", "model": c.LAB_CPR_MODEL, "marker": "CPC-test-END",
            "marker_path": str(directory / "marker.txt"), "return_code": 1,
            "connection": {"url": c.LAB_CPR_URL}}))
        (directory / "events.jsonl").write_text(json.dumps({
            "type": "error", "message": "ChatGPT personalization preflight exceeded its readiness deadline"}))
        (self.root / "checks/current.json").write_text(json.dumps({"directory": str(directory)}))
        with mock.patch.object(c, "state_directory", return_value=self.root), \
                mock.patch.object(c, "saved_connection", return_value=None), \
                mock.patch.object(c, "official_client", side_effect=RuntimeError("attempted a client install")) as install:
            self.assertEqual(c.native_setup(["--lab"]), 1)
            install.assert_not_called()
            report = json.loads((self.root / "native-installation.json").read_text())
            self.assertEqual(report["code"], "UPSTREAM_READINESS")
            self.assertIn("chatgpt-web/gpt-6-tools-persist", report["hint"])
            for arguments in (["--lab", "--repair"], ["--lab", "--model", "chatgpt-web/gpt-6-tools-persist"]):
                with self.assertRaisesRegex(RuntimeError, "attempted a client install"):
                    c.native_setup(arguments)
            self.assertEqual(install.call_count, 2)

    def test_check_results_identify_the_endpoint_actually_tested(self):
        endpoint = "http://127.0.0.1:18082/v1"
        for name, status, code in (("pending", "running", "CHECK_PENDING"),
                                   ("failed", "terminal", "UPSTREAM_READINESS"),
                                   ("passed", "terminal", "READY"),
                                   ("mismatch", "terminal", "OUTPUT_MISMATCH"),
                                   ("legacy", "running", "CHECK_PENDING")):
            with self.subTest(name=name):
                directory = self.root / name
                directory.mkdir()
                marker_path = str(directory / "marker.txt")
                record = {"status": status, "model": c.LAB_CPR_MODEL,
                          "connection": {"url": endpoint}, "marker": "CPC-test-END",
                          "marker_path": marker_path,
                          "return_code": 0 if name in ("passed", "mismatch") else 1}
                if name == "legacy":
                    record.pop("connection")
                (directory / "state.json").write_text(json.dumps(record))
                if name in ("passed", "mismatch"):
                    events = [{"type": "item.completed", "item": {"type": "command_execution",
                               "exit_code": 0, "command": marker_path,
                               "aggregated_output": record["marker"]}}, {"type": "turn.completed"}]
                    (directory / "final.txt").write_text(record["marker"] if name == "passed" else "wrong")
                else:
                    events = [{"type": "error", "message": "ChatGPT personalization preflight exceeded its readiness deadline"}]
                (directory / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events))
                result = c.check_result(directory)
                self.assertEqual(result["code"], code)
                self.assertEqual(result["endpoint"], None if name == "legacy" else endpoint)
                self.assertNotEqual(result["endpoint"], c.LAB_CPR_URL)

    def test_migration_copies_wal_and_retains_source(self):
        source, dest = self.root / "old", self.root / "new"
        source.mkdir()
        original = {"state_5.sqlite": b"db", "state_5.sqlite-wal": b"wal", "state_5.sqlite-shm": b"shm",
                    "queue_1.sqlite": b"queue"}
        for filename, content in original.items():
            (source / filename).write_bytes(content)
        with mock.patch.object(c.subprocess, "run", return_value=mock.Mock(stdout="")):
            c.migrate_sqlite_when_idle(source, dest)
        for filename, content in original.items():
            self.assertEqual((dest / filename).read_bytes(), content)
            self.assertEqual((source / filename).read_bytes(), content)
            if os.name != "nt":
                self.assertEqual((dest / filename).stat().st_mode & 0o777, 0o600)

    def test_migration_does_not_mix_databases_or_interrupt_writer(self):
        source, dest = self.root / "old", self.root / "new"
        source.mkdir()
        (source / "state_5.sqlite").write_bytes(b"old")
        dest.mkdir()
        (dest / "state_5.sqlite").write_bytes(b"new")
        with self.assertRaisesRegex(ValueError, "均有数据"):
            c.migrate_sqlite_when_idle(source, dest)
        self.assertEqual((dest / "state_5.sqlite").read_bytes(), b"new")
        (dest / "state_5.sqlite").unlink()
        running_process = '"codex.exe"\n' if os.name == "nt" else "codex\n"
        with mock.patch.object(c.subprocess, "run", return_value=mock.Mock(stdout=running_process)):
            with self.assertRaisesRegex(ValueError, "仍有 Codex"):
                c.migrate_sqlite_when_idle(source, dest)
        self.assertFalse((dest / "state_5.sqlite").exists())

    def test_conflicting_sqlite_locations_reuse_configured_source_without_data_loss(self):
        source = self.root / "old"
        target = self.root / "system-tmp" / getpass.getuser() / "codex-lab" / "codex-sqlite"
        source.mkdir()
        target.mkdir(parents=True)
        (source / "state_5.sqlite").write_bytes(b"existing-history")
        (target / "state_5.sqlite").write_bytes(b"different-history")
        output = io.StringIO()
        with mock.patch.object(c.sys, "platform", "darwin"), \
                mock.patch.object(c.tempfile, "gettempdir", return_value=str(self.root / "system-tmp")), \
                mock.patch.object(c, "provider_settings", return_value={"sqlite_home": str(source)}), \
                mock.patch.dict(os.environ, {"CODEX_SQLITE_HOME": ""}), \
                contextlib.redirect_stderr(output):
            chosen = c.lab_sqlite_home()
            self.assertEqual(Path(os.environ["CODEX_SQLITE_HOME"]), source.resolve())
        self.assertEqual(Path(chosen), source.resolve())
        self.assertEqual((source / "state_5.sqlite").read_bytes(), b"existing-history")
        self.assertEqual((target / "state_5.sqlite").read_bytes(), b"different-history")
        self.assertIn("[SQLITE_PRESERVED]", output.getvalue())
        self.assertIn("--sqlite-home", output.getvalue())
        self.assertIn(str(target), output.getvalue())

    def test_explicit_sqlite_selection_overrides_config_and_never_migrates(self):
        source = self.root / "old"
        target = self.root / "new"
        source.mkdir()
        target.mkdir()
        (source / "state_5.sqlite").write_bytes(b"old")
        (target / "state_5.sqlite").write_bytes(b"new")
        with mock.patch.object(c, "provider_settings", return_value={"sqlite_home": str(source)}), \
                mock.patch.object(c, "migrate_sqlite_when_idle", side_effect=AssertionError("unwanted copy")), \
                mock.patch.dict(os.environ, {"CODEX_SQLITE_HOME": str(target)}):
            self.assertEqual(Path(c.lab_sqlite_home()), target.resolve())
            self.assertEqual(Path(c.lab_sqlite_home(str(source))), source.resolve())
            self.assertEqual(Path(os.environ["CODEX_SQLITE_HOME"]), source.resolve())
        self.assertEqual((source / "state_5.sqlite").read_bytes(), b"old")
        self.assertEqual((target / "state_5.sqlite").read_bytes(), b"new")
        with self.assertRaisesRegex(ValueError, "绝对路径"):
            c.lab_sqlite_choice("relative/path")

    def test_running_codex_defers_migration_and_preserves_source(self):
        source = self.root / "existing"
        source.mkdir()
        (source / "state_5.sqlite").write_bytes(b"live")
        running_process = '"codex.exe"\n' if os.name == "nt" else "codex\n"
        with mock.patch.object(c.sys, "platform", "darwin"), \
                mock.patch.object(c.tempfile, "gettempdir", return_value=str(self.root / "tmp")), \
                mock.patch.object(c, "provider_settings", return_value={"sqlite_home": str(source)}), \
                mock.patch.dict(os.environ, {"CODEX_SQLITE_HOME": ""}), \
                mock.patch.object(c, "secure_directory",
                                  side_effect=lambda path: Path(path).mkdir(parents=True, exist_ok=True)), \
                mock.patch.object(c.subprocess, "run", return_value=mock.Mock(stdout=running_process)), \
                contextlib.redirect_stderr(io.StringIO()) as output:
            chosen = c.lab_sqlite_home()
        self.assertEqual(Path(chosen), source.resolve())
        self.assertEqual((source / "state_5.sqlite").read_bytes(), b"live")
        self.assertIn("[SQLITE_PRESERVED]", output.getvalue())

    def test_cli_sqlite_home_flag_reaches_lab_selector(self):
        chosen = str(self.root / "existing")
        with mock.patch.object(c, "state_directory", return_value=self.root), \
                mock.patch.object(c, "saved_connection", return_value=None), \
                mock.patch.object(c, "lab_sqlite_home",
                                  side_effect=ValueError("stop-before-network")) as select, \
                self.assertRaisesRegex(ValueError, "stop-before-network"):
            c.native_setup(["--lab", "--sqlite-home", chosen])
        select.assert_called_once_with(chosen)

    def test_lab_mode_selects_official_client_and_passes_contract(self):
        catalog = self.root / "models.json"
        catalog.write_text(json.dumps({"models": [{"slug": c.LAB_CPR_MODEL, "visibility": "list"}]}))
        captured = {}

        def configure(args, **kwargs):
            captured["config_args"] = args
            captured["unrelated_env_key_excluded"] = "CODEX_PROVIDER_API_KEY" not in os.environ
            return {"url": c.LAB_CPR_URL, "api_key": "never-print-this-test-key"}, catalog

        def native_configure(binary, connection, model, lab_sqlite=None):
            captured.update(binary=binary, model=model, sqlite=lab_sqlite)
            return {"restart_required": False}

        mocks = [
            mock.patch.object(c, "state_directory", return_value=self.root),
            mock.patch.object(c, "saved_connection", return_value=None),
            mock.patch.object(c, "lab_sqlite_home", return_value="/tmp/example/codex-sqlite"),
            mock.patch.object(c, "official_client", return_value=["/verified/official/codex"]),
            mock.patch.object(c, "native_client", side_effect=AssertionError("must not use modified client")),
            mock.patch.object(c, "configure", side_effect=configure),
            mock.patch.object(c, "client_version", return_value="0.162.1"),
            mock.patch.object(c, "native_preflight", return_value={"status": "network_ok"}),
            mock.patch.object(c, "native_configure", side_effect=native_configure),
            mock.patch.object(c, "model_check", return_value={"status": "passed", "code": "READY"}),
            mock.patch.object(c, "native_check_report", return_value={"status": "passed", "code": "READY"}),
            mock.patch.object(c, "lab_client_path"),
        ]
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, {"CODEX_PROVIDER_API_KEY": "unrelated-service-test-secret"}))
            for patch in mocks:
                stack.enter_context(patch)
            self.assertEqual(c.native_setup(["--lab"]), 0)
        self.assertEqual(captured["binary"], ["/verified/official/codex"])
        self.assertEqual(captured["model"], c.LAB_CPR_MODEL)
        self.assertEqual(captured["sqlite"], "/tmp/example/codex-sqlite")
        self.assertEqual(captured["config_args"], ["--url", c.LAB_CPR_URL])
        self.assertTrue(captured["unrelated_env_key_excluded"])
        report = json.loads((self.root / "native-installation.json").read_text())
        self.assertEqual(report["endpoint"], c.LAB_CPR_URL)
        self.assertNotIn("never-print-this-test-key", json.dumps(report))

    def test_macos_uses_local_temp_sqlite_without_ssh(self):
        parent = self.root / "macos-temp" / getpass.getuser() / "codex-lab"
        with mock.patch.object(c.sys, "platform", "darwin"), \
                mock.patch.object(c.tempfile, "gettempdir", return_value=str(self.root / "macos-temp")), \
                mock.patch.object(c, "provider_settings", return_value={}), \
                mock.patch.object(c, "state_directory", return_value=self.root / ".codex"), \
                mock.patch.object(c, "migrate_sqlite_when_idle") as migrate:
            with mock.patch.dict(os.environ, {"CODEX_SQLITE_HOME": ""}):
                dest = c.lab_sqlite_home()
        self.assertEqual(Path(dest), parent / "codex-sqlite")
        self.assertEqual(migrate.call_args.args[1], parent / "codex-sqlite")

    def test_windows_launcher_preserves_existing_client_and_user_path(self):
        entry = self.root / "bin" / "codex.cmd"
        registry = {"Path": r"C:\ExistingTools"}

        def get_value(_key, name):
            return registry[name], 2

        def set_value(_key, name, _reserved, _kind, value):
            registry[name] = value

        winreg = SimpleNamespace(HKEY_CURRENT_USER=1, KEY_READ=1, KEY_SET_VALUE=2,
                                 REG_EXPAND_SZ=2, OpenKey=lambda *args: contextlib.nullcontext(object()),
                                 QueryValueEx=get_value, SetValueEx=set_value)
        with mock.patch.dict(sys.modules, {"winreg": winreg}):
            c.lab_windows_client_path(r"C:\Official Codex\codex.exe", entry)
            self.assertIn('"C:\\Official Codex\\codex.exe" %*', entry.read_text())
            self.assertEqual(registry["Path"], str(entry.parent) + r";C:\ExistingTools")
            c.lab_windows_client_path(r"C:\Official Codex\codex.exe", entry)
            self.assertEqual(registry["Path"].count(str(entry.parent)), 1)

    def test_windows_launcher_does_not_replace_unrelated_file(self):
        entry = self.root / "codex.cmd"
        entry.write_text("@echo old user file\n")
        with self.assertRaisesRegex(ValueError, "无关文件"):
            c.lab_windows_client_path(r"C:\Official\codex.exe", entry)
        self.assertEqual(entry.read_text(), "@echo old user file\n")

    def test_windows_temp_sqlite_directory_is_user_scoped(self):
        base = self.root / "windows-temp"
        with mock.patch.object(c.sys, "platform", "win32"), \
                mock.patch.object(c.tempfile, "gettempdir", return_value=str(base)), \
                mock.patch.object(c, "provider_settings", return_value={}), \
                mock.patch.object(c, "state_directory", return_value=self.root / ".codex"), \
                mock.patch.object(c, "secure_directory"), \
                mock.patch.object(c, "migrate_sqlite_when_idle") as migrate, \
                mock.patch.dict(os.environ, {"CODEX_SQLITE_HOME": ""}):
            dest = c.lab_sqlite_home()
        self.assertEqual(Path(dest), base / getpass.getuser() / "codex-lab" / "codex-sqlite")
        self.assertEqual(migrate.call_args.args[1], Path(dest))

    @unittest.skipIf(os.name == "nt", "POSIX symlink and shell profile behavior")
    def test_unix_launcher_prioritizes_official_binary_without_replacing_old_one(self):
        old = self.root / "old-codex"
        old.write_text("old customized client\n")
        official = self.root / "official-codex"
        official.write_text("official client\n")
        with mock.patch.object(c.Path, "home", return_value=self.root):
            entry = c.lab_client_path([str(official)])
            self.assertTrue(entry.is_symlink())
            self.assertEqual(entry.resolve(), official.resolve())
            self.assertEqual(old.read_text(), "old customized client\n")
            c.lab_client_path([str(official)])
            bashrc = (self.root / ".bashrc").read_text()
            self.assertEqual(bashrc.count("export PATH="), 1)

    def test_legacy_lab_ywl_flag_remains_supported(self):
        # Same parser alias remains available to users of an earlier candidate.
        with mock.patch.object(c, "state_directory", return_value=self.root), \
                mock.patch.object(c, "saved_connection", return_value=None), \
                mock.patch.object(c, "lab_sqlite_home", return_value="/tmp/x/codex-sqlite"), \
                mock.patch.object(c, "official_client", side_effect=ValueError("intentional-stop-before-network")), \
                self.assertRaisesRegex(ValueError, "intentional-stop-before-network"):
            c.native_setup(["--lab-ywl"])


if __name__ == "__main__":
    unittest.main()
