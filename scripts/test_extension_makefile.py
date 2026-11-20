import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


MAKEFILE = (
    Path(__file__).resolve().parents[1] / "makefiles" / "duckdb_extension.Makefile"
)


@unittest.skipUnless(shutil.which("make"), "requires make")
class ExtensionMakefileTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.record = self.root / "invocation.json"
        self.runner_script = self.root / "duckdb" / "scripts" / "ci" / "run_tests.py"

    def write_recorder(self, path, runner, exit_code=0):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "from pathlib import Path\n"
            f"Path({str(self.record)!r}).write_text(json.dumps("
            f"{{'runner': {runner!r}, 'args': sys.argv[1:]}}))\n"
            f"raise SystemExit({exit_code})\n",
            encoding="utf8",
        )
        path.chmod(0o755)

    def run_target(self, target="test_release", **options):
        command = [
            "make",
            "--no-print-directory",
            "-f",
            str(MAKEFILE),
            target,
            "T=--workers 2 'test/sql/two words.test'",
            *[f"{name}={value}" for name, value in options.items()],
        ]
        return subprocess.run(
            command, cwd=self.root, capture_output=True, text=True, timeout=30
        )

    def invocation(self):
        return json.loads(self.record.read_text(encoding="utf8"))

    def test_generated_wrapper_bypasses_legacy_python_runner_in_every_build(self):
        self.write_recorder(self.runner_script, "legacy-python", exit_code=99)
        for build_type in ["release", "debug", "reldebug"]:
            with self.subTest(build_type=build_type):
                wrapper = self.root / "build" / build_type / "test" / "run"
                self.write_recorder(wrapper, "wrapper")
                self.write_recorder(
                    wrapper.parent / "unittest", "unittest", exit_code=99
                )
                result = self.run_target("test_" + build_type)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(
                    self.invocation(),
                    {
                        "runner": "wrapper",
                        "args": ["--workers", "2", "test/sql/two words.test"],
                    },
                )

    def test_python_wrapper_is_invoked_directly(self):
        self.write_recorder(self.runner_script, "legacy-python", exit_code=99)
        self.write_recorder(
            self.root / "build" / "release" / "test" / "run.py", "python-wrapper"
        )
        result = self.run_target()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            self.invocation(),
            {
                "runner": "python-wrapper",
                "args": ["--workers", "2", "test/sql/two words.test"],
            },
        )

    def test_legacy_python_runner_still_receives_native_unittest(self):
        self.write_recorder(self.runner_script, "legacy-python")
        result = self.run_target()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            self.invocation(),
            {
                "runner": "legacy-python",
                "args": [
                    "./build/release/test/unittest",
                    "--workers",
                    "2",
                    "test/sql/two words.test",
                ],
            },
        )

    def test_native_unittest_is_used_when_python_runner_is_missing(self):
        self.write_recorder(
            self.root / "build" / "release" / "test" / "unittest", "native"
        )
        result = self.run_target()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            self.invocation(),
            {"runner": "native", "args": ["--workers", "2", "test/sql/two words.test"]},
        )

    def test_wrapper_failure_is_propagated(self):
        self.write_recorder(
            self.root / "build" / "release" / "test" / "run.py", "wrapper", exit_code=7
        )
        self.assertNotEqual(self.run_target().returncode, 0)

    def test_skip_tests_does_not_invoke_wrapper(self):
        self.write_recorder(
            self.root / "build" / "release" / "test" / "run.py", "wrapper"
        )
        result = self.run_target(SKIP_TESTS=1)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.record.exists())
