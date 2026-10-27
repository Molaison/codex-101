"""Exercise the real shell bootstrap against local immutable fixture payloads."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent


@unittest.skipIf(os.name == "nt" or not shutil.which("bash"), "Unix shell fixture")
class MagpieBootstrapTests(unittest.TestCase):
    def test_magpie_downloads_matching_module_and_forwards_one_command_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            task_root = Path(directory)
            source = task_root / "source"
            source.mkdir()
            (source / "model_catalog.json").write_text('{"models":[{"slug":"fixture"}]}')
            (source / "magpie_support.py").write_text('MATCHED_PAYLOAD = "one-reference"\n')
            (source / "codex_provider.py").write_text(
                '# codex-provider-connect\nimport sys\nimport magpie_support\n'
                'assert sys.argv[1:] == ["_native-setup", "--magpie"]\n'
                'assert magpie_support.MATCHED_PAYLOAD == "one-reference"\n'
                'print("fixture configured through magpie")\n')
            env = dict(os.environ, HOME=str(task_root / "home"),
                       CODEX_HOME=str(task_root / "codex-home"),
                       CODEX_CONNECT_REF="fixture-magpie", CODEX_CONNECT_BASE=source.as_uri())
            result = subprocess.run(["bash", str(ROOT / "install.sh"), "--magpie"],
                                    env=env, capture_output=True, text=True, timeout=25)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("[READY]", result.stdout)
            self.assertIn("fixture configured through magpie", result.stdout)
            self.assertTrue((task_root / "codex-home/provider-connect/magpie_support.py").is_file())


class ModuleIsolationTests(unittest.TestCase):
    def test_support_loads_when_script_directory_is_absent_from_sys_path(self):
        code = ("import runpy; namespace=runpy.run_path(" + repr(str(ROOT / "codex_provider.py"))
                + ",run_name='isolated_fixture'); module=namespace['load_magpie_support'](); "
                + "assert module.CPR_PROVIDER_ID == 'cpr'")
        result = subprocess.run([sys.executable, "-I", "-S", "-c", code],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
