"""Fixture-only coverage for Magpie onboarding; no real Magpie or CPR calls."""

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

import codex_provider as connector


MODEL = connector.LAB_CPR_MODEL
QUALIFIED = "cpr/" + MODEL
CHECK_MODEL = connector.LAB_CPR_CHECK_MODEL
CHECK_QUALIFIED = "cpr/" + CHECK_MODEL


class MagpieOnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.environment = mock.patch.dict(os.environ, {
            "HOME": str(self.home),
            "CODEX_HOME": str(self.home / ".codex"),
        }, clear=False)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        os.environ.pop("CODEX_PROVIDER_API_KEY", None)

        self.source = self.home / "catalog-source.json"
        self.source.write_text(json.dumps({
            "catalog_marker": {"keep": ["all", "metadata"]},
            "models": [
                {
                    "slug": MODEL, "display_name": "CPR primary",
                    "visibility": "list", "supported_in_api": True,
                    "multi_agent_version": "v1", "default_reasoning_level": "high",
                    "supported_reasoning_levels": [{"effort": "high"}],
                    "custom_model_field": {"nested": 11},
                },
                {
                    "slug": "chatgpt-web/other-v1",
                    "display_name": "Secondary raw model",
                    "visibility": "list", "supported_in_api": True,
                    "multi_agent_version": "v1", "default_reasoning_level": "medium",
                    "supported_reasoning_levels": [{"effort": "medium"}],
                    "custom_model_field": ["preserve", 23],
                },
                {
                    "slug": CHECK_MODEL, "display_name": "CPR self-check",
                    "visibility": "list", "supported_in_api": True,
                    "multi_agent_version": "v1", "default_reasoning_level": "none",
                    "supported_reasoning_levels": [{"effort": "none"}],
                    "custom_model_field": {"nested": 13},
                },
            ],
        }), encoding="utf-8")
        initial = {"url": connector.LAB_CPR_URL, "api_key": "remote-fixture-key",
                   "catalog_source": str(self.source)}
        self.builtin = mock.patch.object(connector, "builtin_catalog", return_value=[{"slug": MODEL, "visibility": "list"}])
        self.builtin.start()
        self.addCleanup(self.builtin.stop)
        self.catalog = connector.native_catalog(initial, [], v1_only=True)
        self.original_config = "# original fixture config\n"
        self.config = connector.config_path()
        self.config.write_text(self.original_config, encoding="utf-8")

    def test_per_node_checks_do_not_reuse_another_nodes_readiness(self):
        connection = {"magpie_per_node": True}
        with mock.patch.object(connector.socket,"getfqdn",return_value="node-a.fixture"):
            first=connector.checks_directory(connection)
        with mock.patch.object(connector.socket,"getfqdn",return_value="node-b.fixture"):
            second=connector.checks_directory(connection)
        self.assertNotEqual(first,second)
        self.assertEqual(first.parent,second.parent)
        self.assertEqual(connector.checks_directory({},per_node=False),connector.state_directory()/"checks")

    def test_mode_switch_preserves_running_check_in_previous_directory(self):
        for old_mode in (False,True):
            with self.subTest(previous_per_node=old_mode):
                previous={"magpie_per_node":old_mode}
                prior=connector.checks_directory(previous)/"current.json"
                prior.parent.mkdir(parents=True,exist_ok=True)
                prior.write_text(json.dumps({"directory":str(self.home/"old-check")}))
                support=types.SimpleNamespace(node_mode=lambda:not old_mode)
                with mock.patch.object(connector,"saved_connection",return_value=previous), \
                        mock.patch.object(connector,"load_magpie_support",return_value=support), \
                        mock.patch.object(connector,"check_result",return_value={"status":"running","code":"CHECK_RUNNING"}), \
                        mock.patch.object(connector,"native_check_report",side_effect=lambda value:value), \
                        mock.patch.object(connector,"official_client") as install, \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(connector.native_setup(["--lab"] if old_mode else ["--magpie"]),2)
                install.assert_not_called()
                self.assertTrue(prior.exists())
                prior.unlink()

    def gateway_module(self, prepare):
        module = types.ModuleType("magpie_support")
        module.prepare = prepare
        return module

    def default_prepare(self, calls, model=None, prefix=True):
        selected = model or QUALIFIED
        def prepare(key, models, raw_model):
            calls.append((key, list(models), raw_model))
            if prefix:
                gateway_model = "cpr/" + raw_model
            else:
                gateway_model = selected
            self.config.write_text(
                'model_provider = "magpie_local"\n'
                'model = %s\n\n'
                '[model_providers.magpie_local]\n'
                'name = "Magpie local gateway"\n'
                'base_url = "%s"\n'
                'wire_api = "responses"\n'
                'experimental_bearer_token = "gateway-fixture-key"\n'
                % (json.dumps(gateway_model), connector.MAGPIE_GATEWAY_URL),
                encoding="utf-8")
        return prepare

    def run_magpie(self, prepare, arguments=(), check_result=None):
        configure_calls = []
        prefight_calls = []

        def native_configure(binary, connection, model, **kwargs):
            configure_calls.append((
                json.loads(json.dumps(connection)),
                model,
                json.loads(json.dumps(kwargs)),
            ))
            return {
                "shared_server_updated": False, "restart_required": False,
                "server_identity": None, "visible_models": [QUALIFIED],
                "config_backup": None,
            }

        def preflight(connection, model):
            prefight_calls.append((json.loads(json.dumps(connection)), model))
            return {"status": "network_ok", "code": "ENDPOINT_OK"}

        check = mock.Mock(return_value=check_result or {
            "status": "passed", "code": "READY", "model": CHECK_QUALIFIED,
            "endpoint": connector.LAB_CPR_URL,
        })
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.dict(sys.modules, {"magpie_support": prepare["module"]}), \
                mock.patch.object(connector, "official_client", return_value=["fixture-codex"]), \
                mock.patch.object(connector, "client_version", return_value="0.162.1"), \
                mock.patch.object(connector, "lab_sqlite_home", return_value=str(self.home / "sqlite")), \
                mock.patch.object(connector, "lab_client_path"), \
                mock.patch.object(connector, "prompt_key", return_value="remote-fixture-key"), \
                mock.patch.object(connector, "native_preflight", side_effect=preflight), \
                mock.patch.object(connector, "native_configure", side_effect=native_configure), \
                mock.patch.object(connector, "model_check", check), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = connector.native_setup([
                "--magpie", "--catalog-file", str(self.source), *arguments,
            ])
        report = json.loads(stdout.getvalue())
        return code, report, configure_calls, check, prefight_calls, stdout.getvalue() + stderr.getvalue()

    def test_database_created_during_copy_prevents_partial_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, dest = root / "source", root / "dest"
            source.mkdir()
            (source / "state_5.sqlite").write_bytes(b"original")
            copy = connector.shutil.copy2
            def concurrent_database(a, b):
                value = copy(a, b)
                (source / "new_runtime.sqlite").write_bytes(b"new state")
                return value
            with mock.patch.object(connector.subprocess, "run", return_value=mock.Mock(stdout="")), \
                    mock.patch.object(connector.shutil, "copy2", side_effect=concurrent_database):
                with self.assertRaisesRegex(ValueError, "发生变化"):
                    connector.migrate_sqlite_when_idle(source, dest)
            self.assertFalse(dest.exists())
            self.assertEqual((source / "state_5.sqlite").read_bytes(), b"original")
            self.assertEqual((source / "new_runtime.sqlite").read_bytes(), b"new state")

    def test_raw_preflight_qualified_check_catalog_and_rerun_generation(self):
        prepare_calls = []
        prepare = self.default_prepare(prepare_calls)
        module = self.gateway_module(prepare)

        code, report, configure_calls, check, preflight_calls, _ = self.run_magpie({
            "module": module,
        })
        self.assertEqual(code, 0)
        self.assertEqual(preflight_calls[0][1], MODEL)
        self.assertEqual(preflight_calls[0][0]["url"], connector.LAB_CPR_URL)
        self.assertEqual(preflight_calls[1][1], CHECK_MODEL)
        self.assertEqual(prepare_calls[0][0], "remote-fixture-key")
        self.assertEqual(prepare_calls[0][1], [MODEL, "chatgpt-web/other-v1", CHECK_MODEL])
        self.assertEqual(prepare_calls[0][2], MODEL)
        self.assertEqual(check.call_args.args[3], CHECK_QUALIFIED)
        self.assertEqual(check.call_args.kwargs["reasoning_effort"], "none")
        self.assertEqual(Path(check.call_args.args[2]).name, "magpie-cpr-models-v1.json")
        self.assertEqual(configure_calls[0][1], QUALIFIED)
        self.assertEqual(Path(configure_calls[0][0]["catalog_file"]).name,
                         "magpie-cpr-models-v1.json")
        self.assertEqual(report["endpoint"], connector.LAB_CPR_URL)
        self.assertEqual(report["transport"], "magpie")
        self.assertEqual(report["gateway"], connector.MAGPIE_GATEWAY_URL)
        self.assertEqual(report["default_model"], MODEL)
        self.assertEqual(report["check_model"], CHECK_QUALIFIED)
        self.assertEqual(report["check_reasoning_effort"], "none")

        saved = json.loads((connector.state_directory() / "connection.json").read_text())
        self.assertEqual(saved["default_model"], MODEL)
        self.assertEqual(saved["check_model"], CHECK_MODEL)
        self.assertEqual(saved["check_reasoning_effort"], "none")
        self.assertEqual(saved["transport"], "magpie")
        raw_catalog_path = saved["catalog_file"]
        self.assertNotEqual(raw_catalog_path,
                            str(connector.state_directory() / "catalogs/magpie-cpr-models-v1.json"))
        self.assertEqual([item["slug"] for item in
                          json.loads(Path(raw_catalog_path).read_text())["models"]],
                         [MODEL, "chatgpt-web/other-v1", CHECK_MODEL])
        first_generation = saved["generation"]

        qualified = connector.state_directory() / "catalogs/magpie-cpr-models-v1.json"
        raw_catalog = json.loads(self.catalog.read_text())
        qualified_catalog = json.loads(qualified.read_text())
        self.assertEqual(qualified_catalog["catalog_marker"], raw_catalog["catalog_marker"])
        self.assertEqual([item["slug"] for item in qualified_catalog["models"]],
                         ["cpr/" + item["slug"] for item in raw_catalog["models"]])
        for raw, copied in zip(raw_catalog["models"], qualified_catalog["models"]):
            self.assertEqual({k: v for k, v in raw.items() if k != "slug"},
                             {k: v for k, v in copied.items() if k != "slug"})
        self.assertEqual(raw_catalog["models"][0]["slug"], MODEL)

        code, report, configure_calls, check, preflight_calls, _ = self.run_magpie({
            "module": module,
        })
        self.assertEqual(code, 0)
        saved = json.loads((connector.state_directory() / "connection.json").read_text())
        self.assertEqual(saved["generation"], first_generation)
        self.assertEqual(check.call_args.kwargs["retry"], False)
        self.assertEqual(configure_calls[0][0]["catalog_file"], str(qualified))
        self.assertEqual(saved["catalog_file"], raw_catalog_path)
        self.assertFalse(any("cpr/cpr/" in item["slug"] for item in
                             json.loads(qualified.read_text())["models"]))

        direct_calls = []
        direct_connection = {
            "url": connector.LAB_CPR_URL, "api_key": "remote-fixture-key",
            "direct": False, "catalog_file": raw_catalog_path,
        }
        direct_check = mock.Mock(return_value={
            "status": "passed", "code": "READY", "model": MODEL,
            "endpoint": connector.LAB_CPR_URL,
        })

        def direct_configure(binary, connection, model, lab_sqlite=None):
            direct_calls.append((dict(connection), model, lab_sqlite))
            return {
                "shared_server_updated": False, "restart_required": False,
                "server_identity": None, "visible_models": [MODEL],
                "config_backup": None,
            }

        direct_stdout = io.StringIO()
        with mock.patch.object(connector, "native_client", return_value=["fixture-codex"]), \
                mock.patch.object(connector, "client_version", return_value="0.162.1"), \
                mock.patch.object(connector, "configure",
                                  return_value=(direct_connection, self.catalog)), \
                mock.patch.object(connector, "native_preflight",
                                  return_value={"status": "network_ok"}), \
                mock.patch.object(connector, "native_configure", side_effect=direct_configure), \
                mock.patch.object(connector, "model_check", direct_check), \
                mock.patch.object(connector, "native_path"), \
                contextlib.redirect_stdout(direct_stdout), \
                contextlib.redirect_stderr(io.StringIO()):
            direct_code = connector.native_setup(["--url", connector.LAB_CPR_URL])
        self.assertEqual(direct_code, 0)
        direct_saved = json.loads((connector.state_directory() / "connection.json").read_text())
        self.assertNotEqual(direct_saved["generation"], first_generation)
        self.assertNotIn("transport", direct_saved)
        self.assertEqual(direct_calls[0][1], MODEL)
        self.assertEqual(sorted(direct_calls[0][0]), sorted(direct_connection))
        self.assertIsNone(direct_calls[0][2])
        self.assertNotIn("transport", json.loads(direct_stdout.getvalue()))

    def test_skip_check_remains_not_verified(self):
        prepare_calls = []
        module = self.gateway_module(self.default_prepare(prepare_calls))
        code, report, configure_calls, check, _, output = self.run_magpie({
            "module": module,
        }, arguments=("--skip-check",))
        self.assertEqual(code, 2)
        self.assertEqual(report["status"], "not_verified")
        self.assertEqual(report["code"], "NOT_VERIFIED")
        self.assertNotIn("READY", output)
        check.assert_not_called()
        self.assertEqual(len(configure_calls), 1)

    def test_check_failure_keeps_saved_default_and_never_reports_ready(self):
        prepare_calls = []
        module = self.gateway_module(self.default_prepare(prepare_calls))
        code, report, configure_calls, check, _, output = self.run_magpie(
            {"module": module},
            check_result={
                "status": "failed", "code": "MODEL_CHECK",
                "model": CHECK_QUALIFIED,
                "endpoint": connector.LAB_CPR_URL,
            },
        )
        self.assertEqual(code, 1)
        self.assertNotEqual(report["status"], "passed")
        self.assertNotIn('"code": "READY"', output)
        self.assertEqual(report["model"], QUALIFIED)
        self.assertEqual(report["default_model"], MODEL)
        self.assertEqual(report["check_model"], CHECK_QUALIFIED)
        self.assertEqual(report["check_reasoning_effort"], "none")
        self.assertEqual(len(configure_calls), 1)
        check.assert_called_once()

    def test_changed_check_model_rotates_generation_but_default_stays_sol(self):
        prepare_calls = []
        module = self.gateway_module(self.default_prepare(prepare_calls))
        first_code, first, _, _, _, _ = self.run_magpie({"module": module})
        first_saved = json.loads((connector.state_directory() / "connection.json").read_text())
        second_code, second, configure_calls, check, _, _ = self.run_magpie(
            {"module": module},
            arguments=("--check-model", "chatgpt-web/other-v1"),
        )
        second_saved = json.loads((connector.state_directory() / "connection.json").read_text())
        self.assertEqual((first_code, second_code), (0, 0))
        self.assertNotEqual(first_saved["generation"], second_saved["generation"])
        self.assertEqual(first["default_model"], MODEL)
        self.assertEqual(second["default_model"], MODEL)
        self.assertEqual(second["check_model"], "cpr/chatgpt-web/other-v1")
        self.assertEqual(second["check_reasoning_effort"], "medium")
        self.assertEqual(second_saved["check_model"], "chatgpt-web/other-v1")
        self.assertEqual(configure_calls[0][1], QUALIFIED)
        self.assertEqual(check.call_args.args[3], "cpr/chatgpt-web/other-v1")

    def test_gateway_validation_accepts_only_fixed_loopback_endpoint(self):
        settings = {
            "model_provider": "magpie_local",
            "model": QUALIFIED,
            "model_providers": {
                "magpie_local": {"base_url": "http://127.0.0.1:3999/v1"},
            },
        }
        with self.assertRaisesRegex(ValueError, "3425"):
            connector.magpie_gateway(MODEL, settings=settings)
        settings["model_providers"]["magpie_local"]["base_url"] = connector.MAGPIE_GATEWAY_URL
        provider_id, provider, model = connector.magpie_gateway(MODEL, settings=settings)
        self.assertEqual((provider_id, model), ("magpie_local", QUALIFIED))
        self.assertEqual(provider["base_url"], connector.MAGPIE_GATEWAY_URL)

    def test_validation_failure_restores_original_config_and_keeps_backup(self):
        prepare_calls = []
        module = self.gateway_module(self.default_prepare(
            prepare_calls, model="not-cpr/raw", prefix=False))
        code, report, configure_calls, check, _, _ = self.run_magpie({
            "module": module,
        })
        self.assertEqual(code, 1)
        self.assertEqual(report["code"], "MAGPIE_SETUP")
        self.assertTrue(report["config_restored"])
        self.assertEqual(self.config.read_text(), self.original_config)
        backup = Path(report["magpie_config_backup"])
        self.assertEqual(backup.read_text(), self.original_config)
        self.assertEqual(backup.parent, connector.state_directory() / "backups")
        self.assertEqual(configure_calls, [])
        check.assert_not_called()

    def test_magpie_native_provider_keeps_local_gateway_auth(self):
        _, qualified_catalog = connector.magpie_catalog(self.catalog)
        native_connection = dict({
            "url": connector.LAB_CPR_URL, "api_key": "must-not-be-used",
            "default_model": MODEL, "generation": "fixture-generation",
            "catalog_file": str(qualified_catalog),
        })
        gateway_provider = {
            "name": "Magpie gateway",
            "base_url": connector.MAGPIE_GATEWAY_URL,
            "wire_api": "responses",
            "http_headers": {
                "x-openai-actor-authorization": "alpha,beta=gamma",
            },
            "auth": {
                "command": "/fixture/magpie-auth",
                "args": ["codex"],
                "timeout_ms": 12345,
                "refresh_interval_ms": 300000,
            },
        }
        expected = {item["slug"] for item in json.loads(qualified_catalog.read_text())["models"]
                    if item.get("visibility") == "list" and item.get("supported_in_api", True)}
        state = {"config": {
            "model_provider": "old", "model": "old-model",
            "model_providers": {"old": {"base_url": "https://old.invalid/v1"}},
            "projects": {"/example": {"trust_level": "untrusted"}},
        }}

        class RPC:
            live = False
            identity = None

            def __init__(self, binary):
                pass

            def call(self, method, params):
                if method == "config/read":
                    return {"config": json.loads(json.dumps(state["config"])), "layers": [
                        {"name": {"type": "user", "file": str(connector.config_path())},
                         "version": "fixture-version"}]}
                if method == "config/batchWrite":
                    for edit in params["edits"]:
                        target = state["config"]
                        parts = edit["keyPath"].split(".")
                        for part in parts[:-1]:
                            target = target.setdefault(part, {})
                        target[parts[-1]] = json.loads(json.dumps(edit["value"]))
                    return {"status": "ok"}
                if method == "model/list":
                    return {"data": [{"model": item} for item in sorted(expected)],
                            "nextCursor": None}
                raise AssertionError(method)

            def __enter__(self):
                return self

            def __exit__(self, *unused):
                pass

        with mock.patch.object(connector, "NativeRPC", RPC), \
                mock.patch.object(connector, "resolved_key",
                                  side_effect=AssertionError("CPR key must not be used")), \
                mock.patch.object(connector, "client_version", return_value="0.162.1"):
            report = connector.native_configure(
                ["fixture-codex"], native_connection, QUALIFIED,
                lab_sqlite=str(self.home / "sqlite"),
                gateway_provider=gateway_provider,
                gateway_provider_id="magpie_local")
        self.assertFalse(report["restart_required"])
        selected = state["config"]["model_providers"]["magpie_local"]
        self.assertEqual(selected["auth"], gateway_provider["auth"])
        self.assertEqual(selected["base_url"], connector.MAGPIE_GATEWAY_URL)
        self.assertEqual(selected["wire_api"], "responses")
        self.assertEqual(selected["http_headers"],
                         {"x-openai-actor-authorization": "alpha,beta=gamma"})
        self.assertEqual(selected["request_max_retries"], connector.LAB_HTTP_RETRIES)
        self.assertEqual(selected["stream_max_retries"], connector.LAB_STREAM_RETRIES)
        self.assertEqual(selected["stream_idle_timeout_ms"], connector.LAB_STREAM_IDLE_TIMEOUT_MS)
        self.assertFalse(selected["supports_websockets"])
        self.assertEqual(state["config"]["model_provider"], "magpie_local")
        self.assertEqual(state["config"]["model"], QUALIFIED)
        self.assertEqual(state["config"]["model_catalog_json"], str(qualified_catalog))
        self.assertFalse((connector.state_directory() / "keys").exists())
        self.assertEqual(state["config"]["projects"]["/example"]["trust_level"], "untrusted")


class SqliteInventoryTests(unittest.TestCase):
    def test_unknown_databases_and_sidecars_move_as_one_inventory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "old"
            destination = root / "new" / "codex-sqlite"
            source.mkdir()
            destination.parent.mkdir()
            expected = {}
            for name, payload in {
                "future_9.sqlite": b"future database",
                "future_9.sqlite-wal": b"future wal",
                "future_9.sqlite-shm": b"future shm",
                "another_2.sqlite": b"second database",
                "another_2.sqlite-wal": b"second wal",
            }.items():
                path = source / name
                path.write_bytes(payload)
                expected[name] = payload
            with mock.patch.object(connector.subprocess, "run",
                                   return_value=mock.Mock(stdout="bash\n", returncode=0)):
                connector.migrate_sqlite_when_idle(source, destination)
            self.assertEqual({path.name: path.read_bytes() for path in destination.iterdir()},
                             expected)
            self.assertEqual({path.name: path.read_bytes() for path in source.iterdir()},
                             expected)


if __name__ == "__main__":
    unittest.main()
