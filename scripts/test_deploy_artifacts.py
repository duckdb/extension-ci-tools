from contextlib import redirect_stdout
import io
from pathlib import Path, PureWindowsPath
import tempfile
import unittest

from unittest import mock

from deploy_artifacts import deploy_extensions, discover_extensions


class DeployArtifactsTest(unittest.TestCase):
    def test_discovery_is_sorted_and_keeps_first_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "a" / "quack.duckdb_extension"
            duplicate = root / "b" / "quack.duckdb_extension"
            wasm = root / "b" / "web.duckdb_extension.wasm"
            for path in (first, duplicate, wasm):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            output = io.StringIO()
            with redirect_stdout(output):
                extensions = discover_extensions(root)
            self.assertEqual(extensions, [("quack", first), ("web", wasm)])
            self.assertIn("skipping duplicate extension 'quack'", output.getvalue())

    @mock.patch("deploy_artifacts.subprocess.run")
    def test_deploy_invokes_script_for_each_extension(self, run):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extension = root / "v1" / "linux_amd64" / "quack.duckdb_extension"
            extension.parent.mkdir(parents=True)
            extension.write_text("binary", encoding="utf-8")
            deploy_extensions(
                root,
                PureWindowsPath(r"duckdb\scripts\extension-upload-single.sh"),
                "abc123",
                "v1.2.3",
                "linux_amd64",
                "bucket",
                True,
                False,
            )
            command = run.call_args.args[0]
            self.assertEqual(command[:8], [
                "bash",
                "duckdb/scripts/extension-upload-single.sh",
                "quack",
                "abc123",
                "v1.2.3",
                "linux_amd64",
                "bucket",
                "true",
            ])
            self.assertEqual(command[8], "false")
            self.assertTrue(command[9])
            self.assertNotIn("\\", command[9])
            run.assert_called_once_with(command, check=True)


if __name__ == "__main__":
    unittest.main()
