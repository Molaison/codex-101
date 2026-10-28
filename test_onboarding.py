"""真实子进程/本地 HTTP 验收安装与恢复协议；模型输出为明确的模拟 fixture。"""
import base64
import contextlib
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
from unittest import mock

import codex_provider as connector


ROOT = Path(__file__).resolve().parent
MODEL = "chatgpt-web/gpt-6-tools-temp"


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.codex = self.home / "native-fixture"
        self.codex.write_text("""#!/usr/bin/env python3
import json,os,sys,time
from pathlib import Path
a=sys.argv[1:]
if '--version' in a: print('codex-cli 0.162.1');sys.exit(0)
if a[:2]==['debug','models']: print('{"models":[]}');sys.exit(0)
counter=Path(os.environ['HOME'])/'calls'
counter.write_text(str(int(counter.read_text())+1) if counter.exists() else '1')
if os.environ.get('FIXTURE_FAIL'):
 print(json.dumps({'type':'error','message':'ChatGPT web turn is missing network access in trusted Codex environment context'}))
 print(json.dumps({'type':'turn.failed','error':{'message':'fixture failure'}}));sys.exit(1)
if os.environ.get('FIXTURE_SLOW'): time.sleep(3)
workspace=Path(a[a.index('-C')+1]);marker=(workspace/'marker.txt').read_text().strip()
print(json.dumps({'type':'item.completed','item':{'type':'command_execution','command':'read '+str(workspace/'marker.txt'),'exit_code':0,'aggregated_output':marker}}))
Path(a[a.index('-o')+1]).write_text(marker[:-4] if os.environ.get('FIXTURE_TRUNCATE') else marker)
print(json.dumps({'type':'turn.completed'}))
""", encoding="utf-8")
        self.codex.chmod(0o700)
        self.env = dict(os.environ, HOME=str(self.home), CODEX_HOME=str(self.home / "codex-home"),
                        CODEX_BINARY=str(self.codex), CODEX_PROVIDER_API_KEY="fixture-secret-do-not-print",
                        NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
        self.env.pop("CODEX_PROVIDER_URL", None)
        self.env.pop("CODEX_PROVIDER_NAME", None)
        self.requests = []
        self.status = 200
        case = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                case.requests.append(self.path)
                body = json.dumps({"models": [{"slug": MODEL, "visibility": "list", "supported_in_api": True}]}).encode()
                self.send_response(case.status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = "http://127.0.0.1:%s/v1" % self.server.server_port

    def run_cli(self, *args, **environment):
        return subprocess.run([sys.executable, str(ROOT / "codex_provider.py")] + list(args),
                              env=dict(self.env, **environment), text=True, capture_output=True, timeout=30)

    def calls(self):
        path = self.home / "calls"
        return int(path.read_text()) if path.exists() else 0

    def test_legacy_setup_reuses_terminal_check_without_changing_configuration(self):
        original = self.home / "codex-home"
        original.mkdir()
        (original / "config.toml").write_text('# existing configuration\n')
        (original / "auth.json").write_text('{"existing":"credential"}\n')
        installed = self.run_cli("setup", "--url", self.url)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        self.assertIn("[READY]", installed.stdout)
        self.assertNotIn(self.env["CODEX_PROVIDER_API_KEY"], installed.stdout + installed.stderr)
        self.assertEqual((original / "config.toml").read_text(), '# existing configuration\n')
        self.assertEqual((original / "auth.json").read_text(), '{"existing":"credential"}\n')
        repeated = self.run_cli("setup", "--url", self.url)
        self.assertEqual(repeated.returncode, 0, repeated.stderr + repeated.stdout)
        self.assertEqual(self.calls(), 1)
        connection = original / "provider-connect/connection.json"
        self.assertEqual(connection.stat().st_mode & 0o777, 0o600)
        current = json.loads((original / "provider-connect/checks/current.json").read_text())
        record = json.loads((Path(current["directory"]) / "state.json").read_text())
        arguments = record["arguments"]
        self.assertEqual(arguments[arguments.index("-s") + 1], "read-only")

    def test_native_check_inherits_saved_permissions_without_rewriting_config(self):
        self.assertEqual(self.run_cli("setup", "--url", self.url, "--skip-check").returncode, 2)
        config = self.home / "codex-home/config.toml"
        config.write_text('sandbox_mode = "danger-full-access"\napproval_policy = "never"\n')
        before = config.read_bytes()
        with mock.patch.dict(os.environ, self.env, clear=True):
            connection = connector.read_connection()
            catalog = connector.state_directory() / "models.json"
            result = connector.model_check([str(self.codex)], connection, catalog, MODEL,
                                           wait_seconds=10, native=True)
        self.assertEqual(result["code"], "READY")
        record = json.loads(Path(result["record"]).read_text())
        arguments = record["arguments"]
        self.assertNotIn("-s", arguments)
        self.assertNotIn("--sandbox", arguments)
        self.assertEqual(arguments[arguments.index("-a") + 1], "never")
        self.assertIn(record["marker_path"], arguments[-1])
        self.assertNotIn(record["marker"], arguments[-1])
        self.assertEqual(config.read_bytes(), before)
        self.assertEqual(self.calls(), 1)

    def test_failed_legacy_setup_is_not_success_or_blindly_retried(self):
        installed = self.run_cli("setup", "--url", self.url, FIXTURE_FAIL="1")
        self.assertEqual(installed.returncode, 1)
        self.assertIn("UPSTREAM_COMPAT", installed.stdout)
        self.assertNotIn("[READY]", installed.stdout)
        report = self.run_cli("doctor", "--check", "--json")
        data = json.loads(report.stdout)
        self.assertEqual(data["code"], "UPSTREAM_COMPAT")
        self.assertEqual(self.calls(), 1)
        repaired = self.run_cli("repair")
        self.assertEqual(repaired.returncode, 0, repaired.stderr + repaired.stdout)
        self.assertEqual(self.calls(), 2)

    def test_truncated_final_fails_even_when_command_and_native_turn_pass(self):
        installed = self.run_cli("setup", "--url", self.url, FIXTURE_TRUNCATE="1")
        self.assertEqual(installed.returncode, 1)
        self.assertIn("OUTPUT_MISMATCH", installed.stdout)
        self.assertNotIn("[READY]", installed.stdout)
        root = self.home / "codex-home/provider-connect/checks"
        directory = Path(json.loads((root / "current.json").read_text())["directory"])
        record = json.loads((directory / "state.json").read_text())
        self.assertRegex(record["marker"], r"^CPC-[0-9a-f]{12}-[0-9a-f]{12}-[0-9a-f]{12}-END$")
        self.assertNotIn(record["marker"], record["arguments"][-1])
        report = self.run_cli("doctor", "--check", "--json")
        self.assertEqual(json.loads(report.stdout)["code"], "OUTPUT_MISMATCH")
        self.assertEqual(self.calls(), 1)

    def test_pending_check_is_recovered_without_second_submission(self):
        prepared = self.run_cli("setup", "--url", self.url, "--skip-check")
        self.assertEqual(prepared.returncode, 2)
        self.assertEqual(self.calls(), 0)
        with mock.patch.dict(os.environ, dict(self.env, FIXTURE_SLOW="1"), clear=True):
            connection = connector.read_connection()
            catalog = connector.state_directory() / "models.json"
            pending = connector.model_check([str(self.codex)], connection, catalog, MODEL, wait_seconds=0)
            self.assertEqual(pending["code"], "CHECK_PENDING")
            recovered = connector.model_check([str(self.codex)], connection, catalog, MODEL, retry=True, wait_seconds=10)
        self.assertEqual(recovered["code"], "READY")
        self.assertEqual(self.calls(), 1)

    def test_key_change_cannot_reuse_old_success(self):
        self.assertEqual(self.run_cli("setup", "--url", self.url).returncode, 0)
        changed = self.run_cli("setup", "--url", self.url, CODEX_PROVIDER_API_KEY="another-fixture-key")
        self.assertEqual(changed.returncode, 1)
        self.assertIn("CHECK_CONNECTION_CHANGED", changed.stdout)
        self.assertEqual(self.calls(), 1)

    def test_401_stops_once_preserves_catalog_and_is_redacted(self):
        self.assertEqual(self.run_cli("setup", "--url", self.url, "--skip-check").returncode, 2)
        catalog = self.home / "codex-home/provider-connect/models.json"
        before = catalog.read_bytes()
        self.status = 401
        self.requests.clear()
        result = self.run_cli("doctor", "--json")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["code"], "AUTH")
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(catalog.read_bytes(), before)
        self.assertEqual(self.calls(), 0)
        self.assertNotIn(self.env["CODEX_PROVIDER_API_KEY"], result.stdout + result.stderr)

    def test_direct_is_explicit_scoped_and_survives_repair(self):
        prepared = self.run_cli("setup", "--url", self.url, "--direct", "--skip-check")
        self.assertEqual(prepared.returncode, 2, prepared.stderr)
        with mock.patch.dict(os.environ, self.env, clear=True):
            connection = connector.read_connection()
            self.assertTrue(connection["direct"])
            original = os.environ.get("NO_PROXY")
            command, environment = connector.provider_command([str(self.codex)], connection, "models.json", ["-m", "override"])
            self.assertEqual(command[-2:], ["-m", "override"])
            self.assertIn("127.0.0.1", environment["NO_PROXY"])
            self.assertEqual(os.environ.get("NO_PROXY"), original)
        repaired = self.run_cli("repair", "--skip-check")
        self.assertEqual(repaired.returncode, 2)
        self.assertTrue(json.loads((self.home / "codex-home/provider-connect/connection.json").read_text())["direct"])

    def test_all_launches_are_independent_of_existing_shared_server(self):
        with mock.patch.dict(os.environ, self.env, clear=True):
            connection = {"url": self.url, "api_key": "fixture-secret-do-not-print"}
            for arguments in ([], ["resume", "--last"], ["app-server"],
                              ["--no-daemon", "exec", "hello"]):
                command, _ = connector.provider_command([str(self.codex)], connection, "models.json", arguments)
                self.assertEqual(command.count("--no-daemon"), 1)
                self.assertIn('model_provider="provider_connect"', command)
                if arguments:
                    self.assertEqual(command[-len(arguments):], arguments)

    def test_native_preflight_checks_key_without_synchronizing_catalog(self):
        with mock.patch.dict(os.environ, self.env, clear=True):
            connection = {"url": self.url, "api_key": self.env["CODEX_PROVIDER_API_KEY"]}
            self.assertEqual(connector.native_preflight(connection, MODEL)["code"], "ENDPOINT_OK")
            self.status = 401
            rejected = connector.native_preflight(connection, MODEL)
        self.assertEqual(rejected["code"], "AUTH")
        self.assertEqual(self.calls(), 0)
        self.assertEqual(self.requests, ["/v1/models", "/v1/models"])
        self.assertNotIn(connection["api_key"], json.dumps(rejected))

    def test_native_preflight_bad_all_proxy_fails_before_any_turn(self):
        with mock.patch.dict(os.environ, {"ALL_PROXY": "http://127.0.0.1:9", "NO_PROXY": ""}, clear=True):
            result = connector.native_preflight({"url": self.url, "api_key": "private-fixture"}, MODEL)
        self.assertEqual(result["code"], "NETWORK")
        self.assertEqual(self.requests, [])
        self.assertEqual(self.calls(), 0)


class OfficialInstallTests(unittest.TestCase):
    def test_modified_cached_official_binary_is_not_retrusted_by_version_string(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = b'#!/bin/sh\necho "codex-cli 0.162.1"\n'
            archive = io.BytesIO()
            with tarfile.open(fileobj=archive, mode="w:gz") as package:
                member = tarfile.TarInfo("package/vendor/x86_64-unknown-linux-musl/bin/codex")
                member.size, member.mode = len(script), 0o755
                package.addfile(member, io.BytesIO(script))
            blob = archive.getvalue()
            metadata = {"name": "@openai/codex", "version": "0.162.1-linux-x64", "dist": {
                "tarball": "https://registry.npmjs.org/@openai/codex/-/fixture.tgz",
                "integrity": "sha512-" + base64.b64encode(hashlib.sha512(blob).digest()).decode()}}
            downloads = []

            def fetch(_url, destination):
                downloads.append(1)
                destination.write_bytes(blob)

            with mock.patch.dict(os.environ, {"CODEX_HOME": str(root)}), \
                    mock.patch.object(connector.platform, "machine", return_value="x86_64"), \
                    mock.patch.object(connector.sys, "platform", "linux"), \
                    mock.patch.object(connector, "latest_official_version", return_value="0.162.1"), \
                    mock.patch.object(connector.urllib.request, "urlopen", side_effect=lambda *a, **k: io.BytesIO(json.dumps(metadata).encode())), \
                    mock.patch.object(connector, "_download", side_effect=fetch):
                original = connector.official_client()
                Path(original[0]).write_bytes(script + b'# changed without altering --version\n')
                repaired = connector.official_client(upgrade=True)
                self.assertEqual(original, repaired)
                self.assertEqual(Path(repaired[0]).read_bytes(), script)
                self.assertEqual(len(downloads), 2)

    def test_latest_stable_is_resolved_on_install_and_explicit_upgrade_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            latest = ["0.174.0"]
            requests = []
            archives = {}
            for version in ("0.174.0", "0.175.0"):
                archive = io.BytesIO()
                binary = ('#!/bin/sh\necho "codex-cli %s"\n' % version).encode()
                with tarfile.open(fileobj=archive, mode="w:gz") as package:
                    member = tarfile.TarInfo("package/vendor/x86_64-unknown-linux-musl/bin/codex")
                    member.size, member.mode = len(binary), 0o755
                    package.addfile(member, io.BytesIO(binary))
                archives[version] = archive.getvalue()

            def metadata(request, timeout, direct=False):
                url = request if isinstance(request, str) else request.full_url
                requests.append(url)
                if url.endswith("/latest"):
                    return io.BytesIO(json.dumps({"name": "@openai/codex", "version": latest[0]}).encode())
                version = url.rsplit("/", 1)[-1].split("-")[0]
                contents = archives[version]
                return io.BytesIO(json.dumps({"name": "@openai/codex", "version": url.rsplit("/", 1)[-1],
                    "dist": {"tarball": "https://registry.npmjs.org/@openai/codex/-/" + version + ".tgz",
                             "integrity": "sha512-" + base64.b64encode(hashlib.sha512(contents).digest()).decode()}}).encode())

            def download(url, destination, direct=False):
                version = url.rsplit("/", 1)[-1].split(".")[0:3]
                destination.write_bytes(archives[".".join(version)])

            with mock.patch.dict(os.environ, {"CODEX_HOME": str(root)}), \
                    mock.patch.object(connector.platform, "machine", return_value="x86_64"), \
                    mock.patch.object(connector.sys, "platform", "linux"), \
                    mock.patch.object(connector, "open_url", side_effect=metadata), \
                    mock.patch.object(connector, "_download", side_effect=download):
                original = connector.official_client()
                self.assertEqual(connector.client_version(original), "0.174.0")
                latest[0] = "0.175.0"
                self.assertEqual(connector.official_client(), original)
                self.assertEqual(len([item for item in requests if item.endswith("/latest")]), 1)
                updated = connector.official_client(upgrade=True)
                self.assertEqual(connector.client_version(updated), "0.175.0")
                self.assertTrue(Path(original[0]).is_file())
                self.assertNotEqual(original, updated)
                latest[0] = "0.174.0"
                with self.assertRaisesRegex(ValueError, "拒绝隐式降级"):
                    connector.official_client(upgrade=True)

    def test_verified_official_archive_and_integrity_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = io.BytesIO()
            script = b'#!/bin/sh\necho "codex-cli 0.162.1"\n'
            with tarfile.open(fileobj=archive, mode="w:gz") as package:
                member = tarfile.TarInfo("package/vendor/x86_64-unknown-linux-musl/bin/codex")
                member.size, member.mode = len(script), 0o755
                package.addfile(member, io.BytesIO(script))
            blob = archive.getvalue()
            metadata = {"name": "@openai/codex", "version": "0.162.1-linux-x64", "dist": {
                "tarball": "https://registry.npmjs.org/@openai/codex/-/fixture.tgz",
                "integrity": "sha512-" + base64.b64encode(hashlib.sha512(blob).digest()).decode()}}
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(root)}), \
                    mock.patch.object(connector.platform, "machine", return_value="x86_64"), \
                    mock.patch.object(connector.sys, "platform", "linux"), \
                    mock.patch.object(connector, "latest_official_version", return_value="0.162.1"), \
                    mock.patch.object(connector.urllib.request, "urlopen", side_effect=lambda *a, **k: io.BytesIO(json.dumps(metadata).encode())), \
                    mock.patch.object(connector, "_download", side_effect=lambda url, target: target.write_bytes(blob)):
                installed = connector.official_client()
                self.assertEqual(connector.client_version(installed), "0.162.1")
                self.assertFalse(json.loads(connector.client_record_path().read_text())["patched"])
                with mock.patch.object(connector.urllib.request, "urlopen", side_effect=AssertionError("cached client must not fetch metadata")), \
                        mock.patch.object(connector, "_download", side_effect=AssertionError("cached client must not download")):
                    self.assertEqual(connector.official_client(), installed)
                Path(installed[0]).unlink()
                repaired = connector.setup_client()
                self.assertEqual(connector.client_version(repaired), "0.162.1")
                self.assertEqual(len(list((root / "provider-connect/official").glob("*.unusable-*"))), 1)
                shutil.rmtree(root / "provider-connect/official")
                connector.client_record_path().unlink()
                metadata["dist"]["integrity"] = "sha512-invalid"
                with self.assertRaisesRegex(ValueError, "SHA512"):
                    connector.official_client()
                self.assertFalse(connector.client_record_path().exists())


class NativeIntegrationTests(unittest.TestCase):
    """Model native startup snapshots, rather than assuming batchWrite reloads them."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.environment = mock.patch.dict(os.environ, {"HOME": str(self.home),
            "CODEX_HOME": str(self.home / ".codex"), "CODEX_PROVIDER_API_KEY": "fixture-secret"}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.connection = {"url": "https://fixture.invalid/v1", "api_key": "fixture-secret", "generation": "fixture-generation"}
        self.catalog = connector.native_catalog(self.connection, ["fixture-codex"])
        self.expected = [m["slug"] for m in json.loads(self.catalog.read_text())["models"]
                         if m.get("visibility") == "list" and m.get("supported_in_api", True)]

    def rpc_factory(self, live=False):
        owner = self
        owner.config = {"model_provider": "old", "model": "old-model",
                        "model_providers": {"old": {"base_url": "https://old.invalid/v1"}},
                        "projects": {"/example": {"trust_level": "untrusted"}}}
        owner.requests, owner.instances = [], []
        owner.server_identity = [1, 2, 3]
        connector.config_path().write_text('# original configuration\n')
        auth = connector.config_path().with_name("auth.json")
        auth.write_text('{"untouched":"existing credential"}')

        class RPC:
            def __init__(self, binary):
                self.live = live
                self.identity = owner.server_identity if live else None
                self.menu = ["old-model"] if live or owner.config.get("model_provider") == "old" else owner.expected
                owner.instances.append(self)

            def call(self, method, params):
                owner.requests.append((method, params))
                if method == "config/read":
                    return {"config": json.loads(json.dumps(owner.config)), "layers": [
                        {"name": {"type": "user", "file": str(connector.config_path())}, "version": "native-version"}]}
                if method == "config/batchWrite":
                    for edit in params["edits"]:
                        parts = edit["keyPath"].split(".")
                        target = owner.config
                        for part in parts[:-1]:
                            target = target.setdefault(part, {})
                        target[parts[-1]] = edit["value"]
                    return {"status": "ok"}
                if method == "model/list":
                    return {"data": [{"model": model} for model in self.menu], "nextCursor": None}
                raise AssertionError(method)

            def __enter__(self):
                return self

            def __exit__(self, *unused):
                pass
        return RPC

    def test_fresh_native_configuration_reopens_only_owned_stdio(self):
        with mock.patch.object(connector, "NativeRPC", self.rpc_factory()):
            report = connector.native_configure(["fixture-codex"], self.connection, MODEL)
        self.assertFalse(report["restart_required"])
        self.assertEqual(set(report["visible_models"]), set(self.expected))
        self.assertEqual(len(self.instances), 2)
        write = next(params for method, params in self.requests if method == "config/batchWrite")
        self.assertFalse(write["reloadUserConfig"])
        self.assertEqual(write["expectedVersion"], "native-version")
        self.assertEqual(self.config["model_provider"], "provider_connect")
        self.assertEqual(self.config["model_reasoning_effort"], "xhigh")
        self.assertEqual(self.config["model_providers"]["old"]["base_url"], "https://old.invalid/v1")
        self.assertEqual(self.config["projects"]["/example"]["trust_level"], "untrusted")
        self.assertEqual(connector.config_path().with_name("auth.json").read_text(), '{"untouched":"existing credential"}')
        self.assertEqual(Path(report["config_backup"]).read_text(), '# original configuration\n')

    def test_lab_native_config_sets_v1_full_access_and_long_sse_retries(self):
        with mock.patch.object(connector, "NativeRPC", self.rpc_factory()):
            report = connector.native_configure(["fixture-codex"], self.connection,
                                                connector.LAB_CPR_MODEL,
                                                lab_sqlite="/tmp/example/codex-sqlite")
        self.assertFalse(report["restart_required"])
        self.assertEqual(self.config["sqlite_home"], "/tmp/example/codex-sqlite")
        self.assertEqual(self.config["sandbox_mode"], "danger-full-access")
        self.assertEqual(self.config["approval_policy"], "never")
        self.assertEqual(self.config["model_reasoning_effort"], "high")
        provider = self.config["model_providers"]["provider_connect"]
        self.assertEqual(provider["request_max_retries"], 8)
        self.assertEqual(provider["stream_max_retries"], 12)
        self.assertEqual(provider["stream_idle_timeout_ms"], 900000)
        self.assertFalse(provider["supports_websockets"])
        self.assertFalse(self.config["features"]["responses_websockets"])
        self.assertFalse(self.config["features"]["responses_websockets_v2"])
        self.assertFalse(self.config["features"]["multi_agent_v2"]["enabled"])
        self.assertFalse(self.config["features"]["multi_agent_v2_dynamic_tools"])
        self.assertEqual(self.config["projects"]["/example"]["trust_level"], "untrusted")

    def test_lab_native_config_uses_metadata_default_when_high_is_unsupported(self):
        with mock.patch.object(connector, "NativeRPC", self.rpc_factory()):
            connector.native_configure(
                ["fixture-codex"], self.connection,
                connector.LAB_CPR_CHECK_MODEL,
                lab_sqlite="/tmp/example/codex-sqlite")
        self.assertEqual(self.config["model_reasoning_effort"], "none")

    def test_model_check_records_reasoning_effort_and_rejects_changed_effort(self):
        worker = mock.Mock()
        worker.wait.return_value = 0
        initial = {
            "status": "failed", "code": "MODEL_CHECK",
            "model": connector.LAB_CPR_CHECK_MODEL,
            "endpoint": self.connection["url"], "transport": None,
            "reasoning_effort": "none",
        }
        with mock.patch.object(connector.subprocess, "Popen", return_value=worker), \
                mock.patch.object(connector, "check_result", return_value=initial):
            result = connector.model_check(
                ["fixture-codex"], self.connection, self.catalog,
                connector.LAB_CPR_CHECK_MODEL, wait_seconds=0, native=True,
                reasoning_effort="none")
        self.assertEqual(result["reasoning_effort"], "none")
        record_path = Path(result["record"])
        record = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertEqual(record["reasoning_effort"], "none")
        self.assertIn("model_reasoning_effort=none", record["arguments"])
        current = json.loads((connector.state_directory() / "checks/current.json").read_text())
        self.assertEqual(current["reasoning_effort"], "none")
        with mock.patch.object(connector, "check_result", return_value=initial):
            changed = connector.model_check(
                ["fixture-codex"], self.connection, self.catalog,
                connector.LAB_CPR_CHECK_MODEL, wait_seconds=0, native=True,
                reasoning_effort="high")
        self.assertEqual(changed["code"], "CHECK_CONNECTION_CHANGED")

    def test_running_native_server_reports_stale_catalog_without_polling_or_restart(self):
        with mock.patch.object(connector, "NativeRPC", self.rpc_factory(live=True)):
            report = connector.native_configure(["fixture-codex"], self.connection, MODEL)
        self.assertTrue(report["restart_required"])
        self.assertFalse(report["shared_server_updated"])
        self.assertEqual(len(self.instances), 1)
        self.assertEqual(sum(method == "model/list" for method, _ in self.requests), 1)

    def test_restart_required_never_submits_check_or_reports_ready(self):
        output = io.StringIO()
        with mock.patch.object(connector, "native_client", return_value=["fixture-codex"]), \
                mock.patch.object(connector, "client_version", return_value="0.162.1"), \
                mock.patch.object(connector, "native_preflight", return_value={"status": "network_ok"}), \
                mock.patch.object(connector, "native_restart", return_value=(False, "SERVER_RESTART_REQUIRED", "restart required")), \
                mock.patch.object(connector, "NativeRPC", self.rpc_factory(live=True)), \
                mock.patch.object(connector, "model_check", side_effect=AssertionError("unexpected model call")), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            code = connector.native_setup(["--url", self.connection["url"]])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["code"], "SERVER_RESTART_REQUIRED")
        self.assertNotIn("fixture-secret", output.getvalue())
        self.assertTrue((connector.state_directory() / "connection.json").is_file())

    def test_same_model_ids_still_require_restart_and_survive_repeated_setup(self):
        factory = self.rpc_factory(live=True)
        original = factory.__init__
        def matching_menu(rpc, binary):
            original(rpc, binary)
            rpc.menu = self.expected
        with mock.patch.object(factory, "__init__", matching_menu), mock.patch.object(connector, "NativeRPC", factory):
            first = connector.native_configure(["fixture-codex"], self.connection, MODEL)
            repeated = connector.native_configure(["fixture-codex"], self.connection, MODEL)
            self.assertTrue(first["restart_required"])
            self.assertTrue(repeated["restart_required"])
            self.assertEqual(sum(method == "config/batchWrite" for method, _ in self.requests), 1)
            self.server_identity = [1, 2, 4]
            fresh = connector.native_configure(["fixture-codex"], self.connection, MODEL)
        self.assertFalse(fresh["restart_required"])
        self.assertFalse((connector.state_directory() / "native-reload.json").exists())

    def test_restart_checks_busy_status_even_with_explicit_confirmation(self):
        rpc = mock.MagicMock(live=True, identity=[1, 2, 3])
        rpc.__enter__.return_value = rpc
        rpc.call.side_effect = [{"data": ["busy-thread"]}, {"thread": {"status": {"type": "active"}}}]
        with mock.patch.object(connector, "NativeRPC", return_value=rpc), \
                mock.patch.object(connector.subprocess, "run") as run:
            result = connector.native_restart(["fixture-codex"], {"server_identity": rpc.identity}, confirmed=True)
        self.assertEqual(result[1], "SERVER_BUSY")
        run.assert_not_called()

    def test_restart_checks_again_after_confirmation_before_official_restart(self):
        rpc = mock.MagicMock(live=True, identity=[1, 2, 3])
        rpc.__enter__.return_value = rpc
        with mock.patch.object(connector, "NativeRPC", return_value=rpc), \
                mock.patch.object(connector, "native_server_idle", side_effect=[True, False]), \
                mock.patch.object(connector.subprocess, "run") as run:
            result = connector.native_restart(["fixture-codex"], {"server_identity": rpc.identity}, confirmed=True)
        self.assertEqual(result[1], "SERVER_BUSY")
        run.assert_not_called()

    def test_idle_confirmed_restart_uses_official_command_and_new_server_identity(self):
        old, new = mock.MagicMock(live=True, identity=[1, 2, 3]), mock.MagicMock(live=True, identity=[1, 2, 4])
        old.__enter__.return_value, new.__enter__.return_value = old, new
        old.call.return_value = {"data": [], "nextCursor": None}
        with mock.patch.object(connector, "NativeRPC", side_effect=[old, new]), \
                mock.patch.object(connector.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
            result = connector.native_restart(["fixture-codex"], {"server_identity": old.identity}, confirmed=True)
        self.assertTrue(result[0])
        self.assertEqual(run.call_args[0][0], ["fixture-codex", "app-server", "daemon", "restart"])

    def test_restart_terminal_confirmation_uses_separate_read_and_write_handles(self):
        old, new = mock.MagicMock(live=True, identity=[1, 2, 3]), mock.MagicMock(live=True, identity=[1, 2, 4])
        old.__enter__.return_value, new.__enter__.return_value = old, new
        terminal = mock.mock_open(read_data="y\n")
        with mock.patch.object(connector, "NativeRPC", side_effect=[old, new]), \
                mock.patch.object(connector, "native_server_idle", return_value=True), \
                mock.patch.object(connector.subprocess, "run", return_value=mock.Mock(returncode=0)), \
                mock.patch("builtins.open", terminal):
            result = connector.native_restart(["fixture-codex"], {"server_identity": old.identity})
        self.assertTrue(result[0])
        terminal.assert_any_call("/dev/tty", "w")
        terminal.assert_any_call("/dev/tty", "r")

    def test_changed_native_connection_checks_new_generation_without_repair_flag(self):
        output = io.StringIO()
        with mock.patch.object(connector, "native_client", return_value=["fixture-codex"]), \
                mock.patch.object(connector, "client_version", return_value="0.162.1"), \
                mock.patch.object(connector, "native_preflight", return_value={"status": "network_ok"}), \
                mock.patch.object(connector, "NativeRPC", self.rpc_factory()), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(connector.native_setup(["--url", self.connection["url"], "--skip-check"]), 2)
            with mock.patch.dict(os.environ, {"CODEX_PROVIDER_API_KEY": "changed-private-fixture"}), \
                    mock.patch.object(connector, "model_check", return_value={"status": "passed", "code": "READY"}) as check:
                self.assertEqual(connector.native_setup(["--url", self.connection["url"]]), 0)
        self.assertTrue(check.call_args.kwargs["retry"])
        self.assertTrue(check.call_args.kwargs["native"])

    def test_native_rerun_retries_failed_terminal_check_without_flag_but_reuses_success(self):
        with mock.patch.object(connector, "native_client", return_value=["fixture-codex"]), \
                mock.patch.object(connector, "client_version", return_value="0.162.1"), \
                mock.patch.object(connector, "native_preflight", return_value={"status": "network_ok"}), \
                mock.patch.object(connector, "NativeRPC", self.rpc_factory()), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(connector.native_setup(["--url", self.connection["url"], "--skip-check"]), 2)
            connector.private_json(connector.state_directory() / "checks/current.json", {"directory": str(self.home / "old-check")})
            with mock.patch.object(connector, "check_result", return_value={"status": "failed", "code": "UPSTREAM_COMPAT"}), \
                    mock.patch.object(connector, "model_check", return_value={"status": "passed", "code": "READY"}) as check:
                self.assertEqual(connector.native_setup([]), 0)
                self.assertTrue(check.call_args.kwargs["retry"])
            with mock.patch.object(connector, "check_result", return_value={"status": "passed", "code": "READY"}), \
                    mock.patch.object(connector, "model_check", return_value={"status": "passed", "code": "READY"}) as check:
                self.assertEqual(connector.native_setup([]), 0)
                self.assertFalse(check.call_args.kwargs["retry"])
                self.assertEqual(connector.native_setup(["--repair"]), 0)
                self.assertTrue(check.call_args.kwargs["retry"])

    def test_native_rerun_preserves_pending_check_before_changing_client_or_server(self):
        connector.private_json(connector.state_directory() / "checks/current.json", {"directory": str(self.home / "pending-check")})
        with mock.patch.object(connector, "check_result", return_value={"status": "running", "code": "CHECK_PENDING"}), \
                mock.patch.object(connector, "native_client", side_effect=AssertionError("must not replace client")), \
                mock.patch.object(connector, "native_configure", side_effect=AssertionError("must not mutate server")), \
                mock.patch.object(connector, "model_check", side_effect=AssertionError("must not resubmit")), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(connector.native_setup(["--repair", "--restart-if-idle"]), 2)

    def test_catalog_updates_use_immutable_paths_and_do_not_fetch_upstream(self):
        old = self.catalog.read_bytes()
        with mock.patch.object(connector.urllib.request, "urlopen", side_effect=AssertionError("no upstream sync")):
            self.assertEqual(connector.native_catalog(self.connection, []), self.catalog)
            source = self.home / "new-catalog.json"
            source.write_text(json.dumps({"models": [{"slug": "new-model", "visibility": "list"}]}))
            self.connection["catalog_source"] = str(source)
            new = connector.native_catalog(self.connection, [])
        self.assertNotEqual(new, self.catalog)
        self.assertEqual(self.catalog.read_bytes(), old)
        self.assertEqual(new.stat().st_mode & 0o777, 0o600)
        source.write_text('{"models": []}')
        self.connection["catalog_source"] = str(source)
        with self.assertRaises(ValueError):
            connector.native_catalog(self.connection, [])
        self.assertEqual(self.connection["catalog_file"], str(new))

    def test_explicit_existing_binary_does_not_replace_the_user_entry(self):
        binary = self.home / "official-codex"
        binary.write_bytes(b"fixture executable")
        binary.chmod(0o700)
        entry = self.home / ".local/bin/codex"
        entry.parent.mkdir(parents=True)
        entry.write_text("old entry")
        with mock.patch.dict(os.environ, {"CODEX_BINARY": str(binary)}), \
                mock.patch.object(connector, "client_version", return_value="0.162.1"), \
                mock.patch.object(connector, "resolve_native_binary", return_value=binary):
            self.assertEqual(connector.native_client(), [str(binary)])
        self.assertFalse(entry.is_symlink())
        self.assertEqual(entry.read_text(), "old entry")
        backups = list((connector.state_directory() / "backups").glob("codex-entry-*"))
        self.assertEqual(backups, [])
        self.assertFalse((entry.parent / "codex-provider").exists())

    def test_repair_owned_damaged_catalog_before_starting_native_rpc(self):
        self.catalog.write_text('{broken fixture')
        recovered = connector.native_catalog(self.connection, [])
        self.assertEqual(recovered, self.catalog)
        self.assertTrue(self.connection["catalog_repaired"])
        self.assertEqual(json.loads(recovered.read_text())["models"][0]["slug"],
                         json.loads((ROOT / "model_catalog.json").read_text())["models"][0]["slug"])
        backups = list((connector.state_directory() / "backups").glob("damaged-catalog-*.json"))
        self.assertEqual(backups[0].read_text(), '{broken fixture')
        outside = self.home / "unrelated-models.json"
        outside.write_text('{unrelated broken file')
        self.connection["catalog_file"] = str(outside)
        connector.native_catalog(self.connection, [])
        self.assertEqual(outside.read_text(), '{unrelated broken file')

    def test_shell_installer_uses_private_native_setup_and_bundled_catalog(self):
        source = self.home / "source"
        source.mkdir()
        (source / "model_catalog.json").write_text('{"models":[{"slug":"fixture"}]}')
        (source / "codex_provider.py").write_text('''# codex-provider-connect\nVERSION = "%s"\nimport sys\nassert sys.argv[1] == "_native-setup"\nassert "--url" in sys.argv\nprint("fixture setup passed")\n''' % "0.1.1")
        env = dict(os.environ, PATH=os.environ.get("PATH", "/usr/bin:/bin"),
                   CODEX_CONNECT_BASE=source.as_uri(), CODEX_CONNECT_REF="v0.1.1")
        result = subprocess.run(["bash", str(ROOT / "install.sh"), "--url", self.connection["url"]],
                                env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[READY]", result.stdout)
        self.assertTrue((connector.state_directory() / "codex_provider.py").is_file())
        self.assertTrue((connector.state_directory() / "model_catalog.json").is_file())
        self.assertFalse((self.home / ".local/bin/codex-provider").exists())

    def test_native_path_is_idempotent(self):
        with mock.patch.dict(os.environ, {"SHELL": "/bin/bash"}):
            connector.native_path()
            connector.native_path()
        self.assertEqual((self.home / ".bashrc").read_text().count('export PATH='), 1)

    def test_external_client_does_not_prepend_another_codex_directory(self):
        rc = self.home / ".bashrc"
        rc.write_text("# existing shell settings\n")
        with mock.patch.dict(os.environ, {"SHELL": "/bin/bash"}):
            connector.native_path([str(self.home / "nvm/bin/codex")])
        self.assertEqual(rc.read_text(), "# existing shell settings\n")
        self.assertFalse((self.home / ".profile").exists())

    def test_web_model_uses_api_slug_not_display_name(self):
        expected = "chatgpt-web/gpt-6-tools-temp"
        selected, _ = connector.select_model(self.connection, self.catalog, expected)
        self.assertEqual(selected, expected)
        display = next(m["display_name"] for m in json.loads(self.catalog.read_text())["models"]
                       if m["slug"] == expected)
        self.assertNotEqual(display, expected)
        with self.assertRaises(ValueError):
            connector.select_model(self.connection, self.catalog, display)


class NativeClientReuseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        environment = mock.patch.dict(os.environ, {"HOME": str(self.home),
            "CODEX_HOME": str(self.home / ".codex"), "PATH": "/usr/bin:/bin"}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def client(self, path, version="0.163.0"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('#!/bin/sh\necho "codex-cli ' + version + '"\n')
        path.chmod(0o700)
        return path

    def test_path_client_is_reused_without_downloading_or_replacing_entries(self):
        binary = self.client(self.home / "nvm/bin/codex")
        entry = self.home / ".local/bin/codex"
        entry.parent.mkdir(parents=True)
        entry.write_text("preserve this existing launcher")
        with mock.patch.object(connector.shutil, "which", return_value=str(binary)), \
                mock.patch.object(connector, "official_client", side_effect=AssertionError("must not download")):
            self.assertEqual(connector.native_client(), [str(binary)])
        self.assertEqual(entry.read_text(), "preserve this existing launcher")

    def test_registered_download_is_reused_when_path_is_empty(self):
        binary = self.client(self.home / "downloaded/codex")
        connector.private_json(connector.client_record_path(), {
            "path": str(binary), "source": "official-npm", "version": "0.163.0"})
        with mock.patch.object(connector.shutil, "which", return_value=None), \
                mock.patch.object(connector, "official_client", side_effect=AssertionError("must not download")), \
                mock.patch.object(connector, "resolve_native_binary", return_value=binary):
            selected = connector.native_client()
        self.assertEqual(Path(selected[0]).resolve(), binary.resolve())

    def test_unregistered_cached_download_is_reused(self):
        binary = self.client(connector.state_directory() / "official/0.163.0-linux-x64/package/vendor/x86_64-unknown-linux-musl/bin/codex")
        with mock.patch.object(connector.shutil, "which", return_value=None), \
                mock.patch.object(connector, "official_client", side_effect=AssertionError("must not download")), \
                mock.patch.object(connector, "resolve_native_binary", return_value=binary):
            selected = connector.native_client()
        self.assertEqual(Path(selected[0]).resolve(), binary.resolve())

    def test_standalone_download_is_reused(self):
        binary = self.client(self.home / ".codex/packages/standalone/current/bin/codex")
        with mock.patch.object(connector.shutil, "which", return_value=None), \
                mock.patch.object(connector, "official_client", side_effect=AssertionError("must not download")), \
                mock.patch.object(connector, "resolve_native_binary", return_value=binary):
            selected = connector.native_client()
        self.assertEqual(Path(selected[0]).resolve(), binary.resolve())

    def test_existing_client_is_preserved_without_a_version_pin(self):
        binary = self.client(self.home / "bin/codex", "0.160.0")
        with mock.patch.object(connector.shutil, "which", return_value=str(binary)), \
                mock.patch.object(connector, "official_client", side_effect=AssertionError("must not download")):
            self.assertEqual(connector.native_client(), [str(binary)])
        self.assertFalse((self.home / ".local/bin/codex").exists())

    def test_only_absent_clients_trigger_one_download(self):
        binary = self.client(self.home / "downloaded/codex", "0.162.1")
        with mock.patch.object(connector.shutil, "which", return_value=None), \
                mock.patch.object(connector, "official_client", return_value=[str(binary)]) as download, \
                mock.patch.object(connector, "resolve_native_binary", return_value=binary):
            first = connector.native_client()
            second = connector.native_client()
        self.assertEqual(first, second)
        self.assertEqual(download.call_count, 1)


class ReleaseBootstrapTests(unittest.TestCase):
    """Exercise one unchanged latest URL; only release hosting/setup are fixtures."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.root = self.home / ".codex/provider-connect"
        self.env = {"HOME": str(self.home), "CODEX_HOME": str(self.home / ".codex"),
                    "PATH": os.environ.get("PATH", os.defpath), "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}
        self.latest = "v0.1.1"  # Exercise the supported pre-manifest migration boundary.
        major, minor, patch = self.latest[1:].split(".")
        self.future = "v%s.%s.%s" % (major, minor, int(patch) + 1)
        self.requests = []
        self.missing_catalog = False
        self.mismatch = False
        self.flip_during_download = False
        self.stable = None
        case = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                case.requests.append(self.path)
                if self.path == "/releases/latest/download/install.sh":
                    source = (ROOT / "install.sh").read_text().replace("v" + connector.VERSION, case.latest)
                    body = (source.replace("https://github.com/Molaison/codex-101", case.base)
                                  .replace("https://api.github.com", case.base)).encode()
                elif self.path == "/repos/Molaison/codex-101/releases?per_page=30":
                    assets = [{"name": name} for name in
                              ("install.sh", "install.ps1", "codex_provider.py", "model_catalog.json")]
                    releases = [{"tag_name": case.latest, "draft": False, "prerelease": True, "assets": assets}]
                    if case.stable:
                        releases.append({"tag_name": case.stable, "draft": False,
                                         "prerelease": False, "assets": assets})
                    body = json.dumps(releases).encode()
                else:
                    parts = self.path.split("/")
                    if len(parts) != 5 or parts[1:3] != ["releases", "download"]:
                        self.send_error(404)
                        return
                    tag, filename = parts[3:]
                    if filename == "codex_provider.py":
                        version = "0.0.0" if case.mismatch else tag[1:]
                        body = ('''# codex-provider-connect; fixture setup only, no real model\nVERSION = "%s"\nimport json,sys\nfrom pathlib import Path\nassert sys.argv[1] == "_native-setup"\nroot=Path(__file__).parent\nassert json.loads((root/"model_catalog.json").read_text())["models"][0]["display_name"] == "release-" + VERSION\nprint("fixture setup passed")\n''' % version).encode()
                        if case.flip_during_download:
                            case.latest = case.future
                    elif filename == "model_catalog.json" and not case.missing_catalog:
                        body = json.dumps({"models": [{"slug": MODEL, "display_name": "release-" + tag[1:]}]}).encode()
                    else:
                        self.send_error(404)
                        return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = "http://127.0.0.1:%s" % self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def install(self):
        # This command is identical before/after a new release. No ref override.
        return subprocess.run(["bash", "-o", "pipefail", "-c",
            'curl --http1.1 -fsSL "$1/releases/latest/download/install.sh" | bash', "fixture", self.base],
            env=self.env, capture_output=True, text=True, timeout=20)

    def test_same_latest_url_updates_without_mixing_releases_when_latest_changes_mid_download(self):
        self.flip_during_download = True
        installed = self.install()
        self.assertEqual(installed.returncode, 0, installed.stderr)
        self.assertIn('VERSION = "' + "0.1.1" + '"', (self.root / "codex_provider.py").read_text())
        updated = self.install()
        self.assertEqual(updated.returncode, 0, updated.stderr)
        self.assertIn('VERSION = "' + self.future[1:] + '"', (self.root / "codex_provider.py").read_text())
        self.assertEqual(self.requests, ["/releases/latest/download/install.sh",
            "/repos/Molaison/codex-101/releases?per_page=30",
            "/releases/download/v0.1.1/codex_provider.py",
            "/releases/download/v0.1.1/model_catalog.json",
            "/releases/latest/download/install.sh", "/repos/Molaison/codex-101/releases?per_page=30",
            "/releases/download/" + self.future + "/codex_provider.py",
            "/releases/download/" + self.future + "/model_catalog.json"])

    def test_repeated_same_tag_reuses_cached_helper_and_catalog(self):
        first = self.install()
        second = self.install()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("复用本地", second.stderr)
        self.assertEqual(self.requests, ["/releases/latest/download/install.sh",
            "/repos/Molaison/codex-101/releases?per_page=30",
            "/releases/download/v0.1.1/codex_provider.py",
            "/releases/download/v0.1.1/model_catalog.json",
            "/releases/latest/download/install.sh", "/repos/Molaison/codex-101/releases?per_page=30"])

    def test_stable_tag_is_preferred_over_newer_prerelease(self):
        self.stable = "v0.1.0"
        installed = self.install()
        self.assertEqual(installed.returncode, 0, installed.stderr)
        self.assertIn('VERSION = "0.1.0"', (self.root / "codex_provider.py").read_text())

    def test_entry_and_payload_downloads_avoid_broken_http2_streams(self):
        shim = self.home / "bin"
        shim.mkdir()
        calls = self.home / "curl-calls.jsonl"
        curl = shim / "curl"
        curl.write_text('''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
with Path(%r).open("a") as output:output.write(json.dumps(sys.argv[1:]) + "\\n")
if "--http1.1" not in sys.argv:
    sys.stderr.write("curl: (92) simulated broken HTTP/2 stream\\n")
    sys.exit(92)
os.execv(%r, [%r] + sys.argv[1:])
''' % (str(calls), shutil.which("curl"), shutil.which("curl")))
        curl.chmod(0o700)
        self.env["PATH"] = str(shim) + os.pathsep + self.env["PATH"]
        installed = self.install()
        self.assertEqual(installed.returncode, 0, installed.stderr)
        arguments = [json.loads(line) for line in calls.read_text().splitlines()]
        self.assertEqual(len(arguments), 4)
        self.assertTrue(all("--http1.1" in call for call in arguments))
        self.assertIn("[READY]", installed.stdout)

    def test_missing_payload_or_version_mismatch_never_replaces_working_installation(self):
        self.assertEqual(self.install().returncode, 0)
        previous = {name: (self.root / name).read_bytes() for name in ("codex_provider.py", "model_catalog.json")}
        self.latest = self.future  # A distinct tag must be fetched; a cached release is immutable.
        for failure in ("missing_catalog", "mismatch"):
            with self.subTest(failure=failure):
                setattr(self, failure, True)
                failed = self.install()
                self.assertNotEqual(failed.returncode, 0)
                self.assertNotIn("[READY]", failed.stdout)
                for name, content in previous.items():
                    self.assertEqual((self.root / name).read_bytes(), content)
                setattr(self, failure, False)

    def test_same_entry_repairs_empty_owned_helper_but_preserves_unrelated_file(self):
        self.root.mkdir(parents=True)
        helper = self.root / "codex_provider.py"
        helper.write_text("unrelated file")
        refused = self.install()
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(helper.read_text(), "unrelated file")
        helper.write_text("")
        (self.root / "connection.json").write_text('{"integration":"native"}')
        repaired = self.install()
        self.assertEqual(repaired.returncode, 0, repaired.stderr)
        backups = list((self.root / "backups").glob("damaged-helper.*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), b"")

    def test_failed_latest_download_is_not_success(self):
        result = subprocess.run(["bash", "-o", "pipefail", "-c", 'curl --http1.1 -fsSL "$1/missing" | bash', "fixture", self.base],
                                env=self.env, capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("[READY]", result.stdout)
