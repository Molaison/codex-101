"""Local transactional installer tests; all model calls are isolated fixtures."""

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import install_lifecycle as lifecycle


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.root = self.home / ".codex" / "provider-connect"
        self.root.mkdir(parents=True)
        environment = mock.patch.dict(os.environ, {
            "HOME": str(self.home), "CODEX_HOME": str(self.home / ".codex"),
            "LOCALAPPDATA": str(self.home / "localappdata")}, clear=False)
        environment.start()
        self.addCleanup(environment.stop)
        self.config = self.home / ".codex" / "config.toml"
        self.config.write_text('user_setting = "preserved"\n', encoding="utf-8")
        self.key = self.root / "keys" / "private.key"
        self.key.parent.mkdir(parents=True)
        self.key.write_text("secret-unchanged\n", encoding="utf-8")
        self.sqlite = self.home / "database.sqlite"
        self.sqlite.write_bytes(b"historical sessions stay untouched")

    def payload(self, tag, failure=None):
        source = self.home / ("source-" + tag)
        source.mkdir()
        fixture = '''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
root=Path(os.environ["CODEX_HOME"])/"provider-connect"
config=Path(os.environ["CODEX_HOME"])/"config.toml"

def check_result(directory):
    state=json.loads((Path(directory)/"state.json").read_text())
    success=state["status"] == "terminal"
    return {{"status":"passed" if success else "running", "code":"READY" if success else "CHECK_PENDING",
             "endpoint":state["connection"]["url"],"model":state["model"]}}

if __name__ == "__main__":
    if sys.argv[1] != "_native-setup": sys.exit(81)
    config.write_text('user_setting = "preserved"\\nmanaged_release = "{version}"\\n')
    (root/"client.json").write_text(json.dumps({{"path":sys.executable,"source":"official-npm","version":os.environ.get("FIXTURE_CODEX_VERSION", "{version}")}}))
    if os.environ.get("FIXTURE_PENDING"):
        directory=root/"checks"/"one-attempt"
        directory.mkdir(parents=True,exist_ok=True)
        (directory/"state.json").write_text(json.dumps({{"status":"running","model":"fixture","connection":{{"url":"https://fixture.invalid/v1"}}}}))
        (root/"connection.json").write_text(json.dumps({{"url":"https://fixture.invalid/v1"}}))
        (root/"checks"/"current.json").write_text(json.dumps({{"directory":str(directory),"url":"https://fixture.invalid/v1","native":True}}))
        sys.exit(2)
    if os.environ.get("FIXTURE_CRASH_BEFORE_REPORT"): sys.exit(1)
    if os.environ.get("FIXTURE_SERVER_BUSY"):
        (root/"native-installation.json").write_text(json.dumps({{"status":"not_verified","code":"SERVER_BUSY"}}))
        sys.exit(2)
    report={{"status":"failed" if {failed} else "passed", "code":"CLIENT" if {failed} else "READY"}}
    (root/"native-installation.json").write_text(json.dumps(report))
    sys.exit(1 if {failed} else 0)
'''.format(version=tag, failed="True" if failure else "False")
        (source / "codex_provider.py").write_text(fixture, encoding="utf-8")
        (source / "model_catalog.json").write_text('{"models":[{"slug":"fixture"}]}', encoding="utf-8")
        shutil.copyfile(Path(lifecycle.__file__), source / "install_lifecycle.py")
        (source / "magpie_support.py").write_text("# isolated fixture module\n", encoding="utf-8")
        lifecycle.private_json(source / "release_manifest.json", {
            "schema": 1, "release": tag, "codex_channel": "stable",
            "files": {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
                      for name in lifecycle.PAYLOAD}})
        return source

    def install(self, tag, source=None):
        arguments = type("Arguments", (), {"release": tag, "payload_dir": source or self.payload(tag),
                                         "setup": ["--lab"]})()
        return lifecycle.install(self.root, arguments)

    def test_upgrade_retains_old_release_then_rolls_back_offline(self):
        self.assertEqual(self.install("v0.2.0"), 0)
        old_client = (self.root / "client.json").read_bytes()
        self.assertEqual(self.install("v0.2.1"), 0)
        self.assertEqual(lifecycle.read_json(self.root / "active.json")["previous"], "v0.2.0")
        self.assertTrue((self.root / "releases" / "v0.2.0" / "codex_provider.py").is_file())
        with mock.patch.object(lifecycle, "download", side_effect=AssertionError("offline rollback must not download")), \
                mock.patch.object(lifecycle, "codex_busy", return_value=False):
            self.assertEqual(lifecycle.rollback(self.root), 0)
        self.assertEqual(lifecycle.read_json(self.root / "active.json")["active"], "v0.2.0")
        self.assertEqual(lifecycle.read_json(self.root / "active.json")["status"], "ROLLED_BACK_LOCAL")
        self.assertEqual((self.root / "client.json").read_bytes(), old_client)
        self.assertIn('managed_release = "v0.2.0"', self.config.read_text())
        self.assertEqual(self.key.read_text(), "secret-unchanged\n")
        self.assertEqual(self.sqlite.read_bytes(), b"historical sessions stay untouched")
        self.assertIn("codex-101 managed dispatch", (self.root / "codex_provider.py").read_text())
        with mock.patch.object(lifecycle, "codex_busy", return_value=False):
            self.assertEqual(lifecycle.rollback(self.root), 0)  # Idempotent.

    def test_codex_only_upgrade_can_rollback_without_reverting_helper_tag(self):
        source = self.payload("v0.2.0")
        self.assertEqual(self.install("v0.2.0", source), 0)
        before = (self.root / "client.json").read_bytes()
        args = type("Arguments", (), {"release": "v0.2.0", "payload_dir": source,
                                      "setup": ["--lab", "--upgrade-codex"]})()
        with mock.patch.dict(os.environ, {"FIXTURE_CODEX_VERSION": "0.180.0"}):
            self.assertEqual(lifecycle.install(self.root, args), 0)
        record = lifecycle.read_json(self.root / "active.json")
        self.assertEqual(record["active"], record["previous"])
        with mock.patch.object(lifecycle, "codex_busy", return_value=False):
            self.assertEqual(lifecycle.rollback(self.root), 0)
        self.assertEqual((self.root / "client.json").read_bytes(), before)
        self.assertEqual(lifecycle.read_json(self.root / "active.json")["active"], "v0.2.0")

    def test_upgrade_resolves_release_then_downloads_matching_manifest_assets(self):
        self.assertEqual(self.install("v0.2.0"), 0)
        source = self.payload("v0.2.1")
        requested = []
        names = list(lifecycle.PAYLOAD) + ["release_manifest.json", "install.sh", "install.ps1"]
        metadata = [{"tag_name": "v0.2.1", "prerelease": True, "draft": False,
                     "assets": [{"name": name} for name in names]}]

        def download(url, target):
            self.assertIn("/releases/download/v0.2.1/", url)
            name = url.rsplit("/", 1)[-1]
            requested.append(name)
            shutil.copyfile(source / name, target)

        with mock.patch.object(lifecycle.urllib.request, "urlopen", return_value=io.BytesIO(json.dumps(metadata).encode())), \
                mock.patch.object(lifecycle, "download", side_effect=download):
            self.assertEqual(lifecycle.upgrade(self.root), 0)
        self.assertEqual(set(requested), set(lifecycle.PAYLOAD) | {"release_manifest.json"})
        self.assertEqual(lifecycle.read_json(self.root / "active.json")["active"], "v0.2.1")
        self.assertEqual(lifecycle.read_json(self.root / "active.json")["previous"], "v0.2.0")

    def test_upgrade_rejects_older_stable_instead_of_downgrading(self):
        self.assertEqual(self.install("v0.2.1"), 0)
        old_state = (self.root / "active.json").read_bytes()
        names = list(lifecycle.PAYLOAD) + ["release_manifest.json", "install.sh", "install.ps1"]
        metadata = [{"tag_name": "v0.2.0", "prerelease": False, "draft": False,
                     "assets": [{"name": name} for name in names]}]
        with mock.patch.object(lifecycle.urllib.request, "urlopen", return_value=io.BytesIO(json.dumps(metadata).encode())), \
                mock.patch.object(lifecycle, "download", side_effect=AssertionError("must not download")), \
                self.assertRaisesRegex(ValueError, "拒绝.*降级"):
            lifecycle.upgrade(self.root)
        self.assertEqual((self.root / "active.json").read_bytes(), old_state)

    def test_windows_style_payload_root_preserves_codex_home_client_state(self):
        # PowerShell places bootstrap files in LOCALAPPDATA, not CODEX_HOME.
        state_root = self.root
        self.root = self.home / "localappdata" / "codex-provider-connect"
        self.root.mkdir(parents=True)
        self.assertEqual(self.install("v0.2.0"), 0)
        old_client = (state_root / "client.json").read_bytes()
        self.assertEqual(self.install("v0.2.1"), 0)
        self.assertNotEqual((state_root / "client.json").read_bytes(), old_client)
        with mock.patch.object(lifecycle, "codex_busy", return_value=False):
            self.assertEqual(lifecycle.rollback(self.root), 0)
        self.assertEqual((state_root / "client.json").read_bytes(), old_client)

    def test_legacy_flat_install_is_archived_without_touching_credentials(self):
        old_helper = b'# codex-provider-connect\nVERSION = "0.1.1"\n'
        (self.root / "codex_provider.py").write_bytes(old_helper)
        (self.root / "model_catalog.json").write_text('{"models":[{"slug":"legacy"}]}')
        self.assertEqual(self.install("v0.2.0"), 0)
        previous = lifecycle.read_json(self.root / "active.json")["previous"]
        self.assertTrue(previous.startswith("legacy-"))
        self.assertEqual((self.root / "releases" / previous / "codex_provider.py").read_bytes(), old_helper)
        with mock.patch.object(lifecycle, "codex_busy", return_value=False):
            self.assertEqual(lifecycle.rollback(self.root), 0)
        self.assertEqual(lifecycle.read_json(self.root / "active.json")["active"], previous)
        self.assertEqual(self.key.read_text(), "secret-unchanged\n")

    def test_recover_finishes_rollback_interrupted_after_first_file(self):
        self.assertEqual(self.install("v0.2.0"), 0)
        self.assertEqual(self.install("v0.2.1"), 0)
        original = lifecycle.restore_file
        interrupted = [False]

        def fail_after_first(tx, name, record):
            original(tx, name, record)
            if not interrupted[0]:
                interrupted[0] = True
                raise RuntimeError("simulated power loss")

        with mock.patch.object(lifecycle, "codex_busy", return_value=False), \
                mock.patch.object(lifecycle, "restore_file", side_effect=fail_after_first), \
                self.assertRaisesRegex(RuntimeError, "power loss"):
            lifecycle.rollback(self.root)
        self.assertTrue((self.root / "rollback-pending.json").exists())
        with mock.patch.object(lifecycle, "codex_busy", return_value=False):
            self.assertEqual(lifecycle.recover(self.root), 0)
        self.assertFalse((self.root / "rollback-pending.json").exists())
        self.assertEqual(lifecycle.read_json(self.root / "active.json")["status"], "ROLLED_BACK_LOCAL")

    @unittest.skipIf(os.name == "nt", "The local shell binary fixture is Unix-only")
    def test_legacy_client_without_stored_hash_is_checked_before_rollback(self):
        self.assertEqual(self.install("v0.2.0"), 0)
        old_binary = self.home / "previous-official-codex"
        old_binary.write_text('#!/bin/sh\necho "codex-cli 0.160.0"\n')
        old_binary.chmod(0o700)
        lifecycle.private_json(self.root / "client.json", {
            "source": "official-npm", "version": "0.160.0", "path": str(old_binary)})
        self.assertEqual(self.install("v0.2.1"), 0)
        old_binary.write_text(old_binary.read_text() + '# modified with unchanged version\n')
        with mock.patch.object(lifecycle, "codex_busy", return_value=False), \
                self.assertRaisesRegex(ValueError, "SHA256"):
            lifecycle.rollback(self.root)
        self.assertEqual(lifecycle.read_json(self.root / "active.json")["active"], "v0.2.1")

    def test_activation_crash_recovers_without_second_model_call(self):
        self.assertEqual(self.install("v0.2.0"), 0)
        source = self.payload("v0.2.1")
        with mock.patch.object(lifecycle, "install_dispatch", side_effect=RuntimeError("power loss")), \
                self.assertRaisesRegex(RuntimeError, "power loss"):
            self.install("v0.2.1", source)
        self.assertEqual(lifecycle.read_json(self.root / "active.json")["active"], "v0.2.0")
        pointer = lifecycle.read_json(self.root / "pending.json")
        transaction = lifecycle.read_json(self.root / "transactions" / pointer["id"] / "journal.json")
        self.assertEqual(transaction["stage"], "ACTIVATING")
        with mock.patch.object(lifecycle.subprocess, "call", side_effect=AssertionError("no second model call")):
            self.assertEqual(lifecycle.recover(self.root), 0)
        self.assertEqual(lifecycle.read_json(self.root / "active.json")["active"], "v0.2.1")

    def test_busy_shared_server_preserves_active_version_until_recovery(self):
        self.assertEqual(self.install("v0.2.0"), 0)
        config = self.config.read_bytes()
        with mock.patch.dict(os.environ, {"FIXTURE_SERVER_BUSY": "1"}):
            self.assertEqual(self.install("v0.2.1"), 2)
        self.assertEqual(lifecycle.read_json(self.root / "active.json")["active"], "v0.2.0")
        self.assertTrue((self.root / "pending.json").exists())
        self.assertNotEqual(self.config.read_bytes(), config)
        self.assertEqual(lifecycle.recover(self.root), 0)
        self.assertEqual(lifecycle.read_json(self.root / "active.json")["active"], "v0.2.1")

    def test_corrupt_release_is_rejected_without_touching_active_version(self):
        self.assertEqual(self.install("v0.2.0"), 0)
        state = (self.root / "active.json").read_bytes()
        config = self.config.read_bytes()
        source = self.payload("v0.2.1")
        (source / "model_catalog.json").write_text("corrupt")
        with self.assertRaisesRegex(ValueError, "SHA256"):
            self.install("v0.2.1", source)
        self.assertEqual((self.root / "active.json").read_bytes(), state)
        self.assertEqual(self.config.read_bytes(), config)

    def test_unrelated_file_and_dangling_symlink_are_not_replaced(self):
        helper = self.root / "codex_provider.py"
        source = self.payload("v0.2.0")
        helper.write_text("unrelated user file\n")
        with self.assertRaisesRegex(ValueError, "不属于本安装器"):
            self.install("v0.2.0", source)
        self.assertEqual(helper.read_text(), "unrelated user file\n")
        helper.unlink()
        self.config.unlink()
        self.config.symlink_to(self.home / "missing-config.toml")
        with self.assertRaisesRegex(ValueError, "符号链接"):
            self.install("v0.2.0", source)
        self.assertTrue(self.config.is_symlink())
        self.assertFalse((self.root / "active.json").exists())

    def test_local_setup_failure_restores_previous_config_and_client(self):
        self.assertEqual(self.install("v0.2.0"), 0)
        before = self.config.read_bytes()
        client = (self.root / "client.json").read_bytes()
        self.assertEqual(self.install("v0.2.1", self.payload("v0.2.1", failure=True)), 1)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual((self.root / "client.json").read_bytes(), client)
        self.assertEqual(lifecycle.read_json(self.root / "active.json")["active"], "v0.2.0")

    def test_failed_setup_compensation_survives_interruption(self):
        self.assertEqual(self.install("v0.2.0"), 0)
        config = self.config.read_bytes()
        client = (self.root / "client.json").read_bytes()
        original = lifecycle.restore_file
        interrupted = [False]

        def interrupt_after_first(tx, name, item):
            original(tx, name, item)
            if not interrupted[0]:
                interrupted[0] = True
                raise RuntimeError("simulated compensation crash")

        with mock.patch.object(lifecycle, "restore_file", side_effect=interrupt_after_first), \
                self.assertRaisesRegex(RuntimeError, "compensation crash"):
            self.install("v0.2.1", self.payload("v0.2.1", failure=True))
        self.assertTrue((self.root / "pending.json").exists())
        with mock.patch.object(lifecycle.subprocess, "call", side_effect=AssertionError("never resubmit setup")):
            self.assertEqual(lifecycle.recover(self.root), 1)
        self.assertFalse((self.root / "pending.json").exists())
        self.assertEqual(self.config.read_bytes(), config)
        self.assertEqual((self.root / "client.json").read_bytes(), client)
        self.assertEqual(lifecycle.read_json(self.root / "active.json")["active"], "v0.2.0")

    def test_ambiguous_setup_crash_does_not_submit_a_second_turn(self):
        self.assertEqual(self.install("v0.2.0"), 0)
        with mock.patch.object(lifecycle.subprocess, "call", side_effect=RuntimeError("simulated process crash")):
            with self.assertRaisesRegex(RuntimeError, "process crash"):
                self.install("v0.2.1")
        with mock.patch.object(lifecycle.subprocess, "call", side_effect=AssertionError("must not resubmit")):
            self.assertEqual(lifecycle.recover(self.root), 2)
        pointer = lifecycle.read_json(self.root / "pending.json")
        record = lifecycle.read_json(self.root / "transactions" / pointer["id"] / "journal.json")
        self.assertEqual(record["stage"], "RECOVERY_REQUIRED")
        self.assertEqual(lifecycle.read_json(self.root / "active.json")["active"], "v0.2.0")

    def test_stale_upstream_error_cannot_mask_a_new_local_failure(self):
        self.assertEqual(self.install("v0.2.0"), 0)
        state_root = lifecycle.connection_state_root()
        lifecycle.private_json(state_root / "native-installation.json", {
            "status": "failed", "code": "UPSTREAM_READINESS"})
        with mock.patch.dict(os.environ, {"FIXTURE_CRASH_BEFORE_REPORT": "1"}):
            self.assertEqual(self.install("v0.2.1"), 1)
        self.assertEqual(lifecycle.read_json(self.root / "active.json")["active"], "v0.2.0")

    def test_conflicting_user_config_blocks_rollback(self):
        self.assertEqual(self.install("v0.2.0"), 0)
        self.assertEqual(self.install("v0.2.1"), 0)
        self.config.write_text(self.config.read_text() + 'user_added = "later"\n')
        with mock.patch.object(lifecycle, "codex_busy", return_value=False), \
                self.assertRaisesRegex(ValueError, "外部修改|配置或客户端记录"):
            lifecycle.rollback(self.root)
        self.assertEqual(lifecycle.read_json(self.root / "active.json")["active"], "v0.2.1")
        self.assertIn("user_added", self.config.read_text())

    def test_pending_turn_is_not_resubmitted_on_recover(self):
        source = self.payload("v0.2.0")
        with mock.patch.dict(os.environ, {"FIXTURE_PENDING": "1"}):
            self.assertEqual(self.install("v0.2.0", source), 2)
        self.assertEqual(lifecycle.recover(self.root), 2)
        pointer = lifecycle.read_json(self.root / "checks" / "current.json")
        receipt = lifecycle.read_json(Path(pointer["directory"]) / "state.json")
        receipt["status"] = "terminal"
        lifecycle.private_json(Path(pointer["directory"]) / "state.json", receipt)
        with mock.patch.object(lifecycle.subprocess, "call", side_effect=AssertionError("do not resubmit terminal turn")):
            self.assertEqual(lifecycle.recover(self.root), 0)
        self.assertFalse((self.root / "pending.json").exists())
        self.assertEqual(lifecycle.read_json(self.root / "active.json")["status"], "READY")

    def test_busy_codex_defers_rollback_without_mutation(self):
        self.assertEqual(self.install("v0.2.0"), 0)
        self.assertEqual(self.install("v0.2.1"), 0)
        with mock.patch.object(lifecycle, "codex_busy", return_value=True):
            self.assertEqual(lifecycle.rollback(self.root), 2)
        self.assertEqual(lifecycle.read_json(self.root / "active.json")["active"], "v0.2.1")

    @unittest.skipIf(os.name == "nt", "Bash bootstrap belongs to the Unix CI matrix")
    def test_modern_bash_bootstrap_uses_transaction_engine(self):
        source = self.payload("v0.2.0")
        environment = dict(os.environ, CODEX_CONNECT_REF="v0.2.0", CODEX_CONNECT_BASE=source.as_uri())
        script = Path(__file__).resolve().parent / "install.sh"
        result = subprocess.run(["bash", str(script), "--lab"], env=environment,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[READY]", result.stdout)
        self.assertEqual(lifecycle.read_json(self.root / "active.json")["active"], "v0.2.0")


if __name__ == "__main__":
    unittest.main()
