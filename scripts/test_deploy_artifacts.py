from contextlib import redirect_stdout
import io
from pathlib import Path, PureWindowsPath
import subprocess
import tempfile
import unittest

from unittest import mock

from deploy_artifacts import DeploymentError, deploy_extensions, discover_extensions


class DeployArtifactsTest(unittest.TestCase):
    def test_discovery_is_sorted_and_keeps_first_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "v1" / "linux_amd64" / "quack.duckdb_extension"
            duplicate = root / "v2" / "linux_amd64" / "quack.duckdb_extension"
            wasm = root / "v1" / "wasm_eh" / "web.duckdb_extension.wasm"
            for path in (first, duplicate, wasm):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            output = io.StringIO()
            with redirect_stdout(output):
                extensions = discover_extensions(root)
            self.assertEqual(
                extensions,
                [
                    ("linux_amd64", "quack", first),
                    ("wasm_eh", "web", wasm),
                ],
            )
            self.assertIn(
                "skipping duplicate extension 'quack' for linux_amd64",
                output.getvalue(),
            )

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

    @mock.patch("deploy_artifacts.subprocess.run")
    def test_deploys_same_extension_for_each_architecture_serially(self, run):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for architecture in ("osx_arm64", "linux_amd64", "wasm_eh"):
                suffix = (
                    ".duckdb_extension.wasm"
                    if architecture.startswith("wasm_")
                    else ".duckdb_extension"
                )
                extension = root / "v1" / architecture / f"quack{suffix}"
                extension.parent.mkdir(parents=True, exist_ok=True)
                extension.write_text("binary", encoding="utf-8")

            deploy_extensions(
                root,
                PureWindowsPath(r"duckdb\scripts\extension-upload-single.sh"),
                "abc123",
                "v1.2.3",
                "bucket",
                True,
                False,
            )

            self.assertEqual(
                [
                    (call.args[0][5], call.args[0][2])
                    for call in run.call_args_list
                ],
                [
                    ("linux_amd64", "quack"),
                    ("osx_arm64", "quack"),
                    ("wasm_eh", "quack"),
                ],
            )

    @mock.patch("deploy_artifacts.subprocess.run")
    def test_deploy_filters_requested_extensions(self, run):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("httpfs", "json"):
                extension = root / "v1" / "linux_amd64" / f"{name}.duckdb_extension"
                extension.parent.mkdir(parents=True, exist_ok=True)
                extension.write_text("binary", encoding="utf-8")

            deploy_extensions(
                root,
                PureWindowsPath(r"duckdb\scripts\extension-upload-single.sh"),
                "abc123",
                "v1.2.3",
                "bucket",
                False,
                True,
                {"httpfs"},
            )

            command = run.call_args.args[0]
            self.assertEqual(command[2], "httpfs")
            run.assert_called_once_with(command, check=True)

    @mock.patch("deploy_artifacts.subprocess.run")
    def test_deploy_reports_requested_extensions_missing_per_architecture(self, run):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            linux = root / "v1" / "linux_amd64" / "json.duckdb_extension"
            wasm = root / "v1" / "wasm_eh" / "httpfs.duckdb_extension.wasm"
            for extension in (linux, wasm):
                extension.parent.mkdir(parents=True, exist_ok=True)
                extension.write_text("binary", encoding="utf-8")

            with self.assertRaisesRegex(
                DeploymentError,
                "linux_amd64: requested extensions not found: httpfs",
            ):
                deploy_extensions(
                    root,
                    PureWindowsPath(r"duckdb\scripts\extension-upload-single.sh"),
                    "abc123",
                    "v1.2.3",
                    "bucket",
                    False,
                    True,
                    {"httpfs"},
                )
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0][5], "wasm_eh")

    @mock.patch("deploy_artifacts.subprocess.run")
    def test_deploy_continues_after_failure_and_fails_at_end(self, run):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("first", "second"):
                extension = (
                    root / "v1" / "linux_amd64" / f"{name}.duckdb_extension"
                )
                extension.parent.mkdir(parents=True, exist_ok=True)
                extension.write_text("binary", encoding="utf-8")
            run.side_effect = [
                subprocess.CalledProcessError(7, ["upload"]),
                None,
            ]

            with self.assertRaisesRegex(
                DeploymentError,
                "linux_amd64/first: upload command exited with 7",
            ):
                deploy_extensions(
                    root,
                    PureWindowsPath(r"duckdb\scripts\extension-upload-single.sh"),
                    "abc123",
                    "v1.2.3",
                    "bucket",
                    False,
                    True,
                )
            self.assertEqual(run.call_count, 2)
            self.assertEqual(
                [call.args[0][2] for call in run.call_args_list],
                ["first", "second"],
            )

    def test_deploy_fails_when_no_artifacts_are_found(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                FileNotFoundError,
                "no extension binaries found",
            ):
                deploy_extensions(
                    Path(directory),
                    PureWindowsPath(r"duckdb\scripts\extension-upload-single.sh"),
                    "abc123",
                    "v1.2.3",
                    "bucket",
                    False,
                    True,
                )


if __name__ == "__main__":
    unittest.main()
