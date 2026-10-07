from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SCRIPT = SCRIPTS / "yrrp-launch-campaign-build.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load_module():
    spec = importlib.util.spec_from_file_location("yrrp_launch_campaign_build", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load campaign launch script")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def failed(stdout: str = "", stderr: str = "") -> subprocess.CalledProcessError:
    return subprocess.CalledProcessError(
        1, ["ssh", "builder", "python3 -c 'script'"], output=stdout, stderr=stderr
    )


class FormatLaunchErrorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.format_launch_error = staticmethod(load_module().format_launch_error)

    def test_called_process_error_includes_remote_stderr(self) -> None:
        error = failed(
            stderr="Traceback ...\nSystemExit: testkey.pk8 does not point to releasekey.pk8\n"
        )
        text = self.format_launch_error(error)
        self.assertTrue(text.startswith(f"campaign launch error: {error}"))
        self.assertIn("remote stderr:", text)
        self.assertIn("SystemExit: testkey.pk8 does not point to releasekey.pk8", text)

    def test_empty_streams_print_no_remote_label(self) -> None:
        for error in (failed(), failed(stdout="", stderr="")):
            text = self.format_launch_error(error)
            self.assertEqual(f"campaign launch error: {error}", text)
            self.assertNotIn("remote", text)

    def test_long_output_is_trimmed_to_last_40_lines(self) -> None:
        lines = [f"line {number}" for number in range(100)]
        text = self.format_launch_error(failed(stderr="\n".join(lines) + "\n"))
        self.assertIn("line 99", text)
        self.assertIn("line 60", text)
        self.assertNotIn("line 59\n", text + "\n")
        self.assertEqual(40, sum(1 for l in text.splitlines() if l.startswith("line ")))

    def test_stdout_is_labelled_separately(self) -> None:
        text = self.format_launch_error(failed(stdout="progress\n", stderr="boom\n"))
        self.assertIn("remote stdout:", text)
        self.assertIn("progress", text)
        self.assertIn("remote stderr:", text)

    def test_non_subprocess_error_prints_only_first_line(self) -> None:
        self.assertEqual(
            "campaign launch error: bad value",
            self.format_launch_error(ValueError("bad value")),
        )


if __name__ == "__main__":
    unittest.main()
