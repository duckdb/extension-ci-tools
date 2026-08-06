import os
from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parent))

from ci_phase import (  # noqa: E402
    PhaseRunner,
    extra_dependencies,
    is_true,
    test_environment,
    tool_enabled,
)


class RecordingRunner(PhaseRunner):
    def __init__(self, environ):
        super().__init__(environ)
        self.commands = []

    def run(self, command, **kwargs):
        self.commands.append((command, kwargs))

    def run_with_retry(self, command, **kwargs):
        self.commands.append((command, {**kwargs, "retry": True}))


class CIPhaseTest(unittest.TestCase):
    def environment(self, workspace, platform="linux", architecture="linux_amd64"):
        return {
            "CI_PLATFORM": platform,
            "DUCKDB_PLATFORM": architecture,
            "GITHUB_WORKSPACE": str(workspace),
            "CI_EXTENSION_NAME": "quack",
            "CI_EXTENSION_CANONICAL": "",
            "CI_DUCKDB_VERSION": "v1.2.3",
            "CI_BUILD_TYPE": "release",
            "CI_TEST_CONFIG": "{}",
            "CI_EXTENSIONS_TEST_SELECTION": "regular",
        }

    def test_boolean_and_toolchain_parsing(self):
        self.assertTrue(is_true("true"))
        self.assertTrue(is_true("1"))
        self.assertFalse(is_true("false"))
        self.assertTrue(tool_enabled("rust;go", "go"))
        self.assertFalse(tool_enabled("fortran", "go"))

    def test_json_inputs(self):
        self.assertEqual(
            test_environment('{"test_env_variables":{"TOKEN":12,"ENABLED":true}}'),
            {"TOKEN": "12", "ENABLED": "true"},
        )
        self.assertEqual(
            extra_dependencies('{"linux_amd64":["openssl","zlib"]}', "linux_amd64"),
            ["openssl", "zlib"],
        )
        self.assertEqual(extra_dependencies("{}", "linux_amd64"), [])

    def test_checkout_runs_only_requested_operations(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.environment(directory)
            env.update(
                {
                    "CI_DUCKDB_GIT_REPOSITORY": "duckdb/duckdb-fork",
                    "CI_EXTENSION_TAG": "v2.0.0",
                    "CI_DUCKDB_TAG": "v1.2.3-test",
                }
            )
            runner = RecordingRunner(env)
            runner.checkout()
            commands = [command for command, _ in runner.commands]
            self.assertEqual(
                commands,
                [
                    ["make", "set_duckdb_repository"],
                    ["make", "set_duckdb_version"],
                    ["git", "tag", "v2.0.0"],
                    ["make", "set_duckdb_tag"],
                ],
            )

    def test_artifact_paths_for_native_and_wasm(self):
        with tempfile.TemporaryDirectory() as directory:
            native = RecordingRunner(self.environment(directory))
            self.assertEqual(
                native.artifact_path(),
                "build/release/extension/quack/quack.duckdb_extension",
            )
            wasm_env = self.environment(directory, "wasm", "wasm_eh")
            wasm_env["CI_UPLOAD_ALL_EXTENSIONS"] = "true"
            wasm = RecordingRunner(wasm_env)
            self.assertEqual(
                wasm.artifact_path(),
                "build/wasm_eh/repository/**/*.duckdb_extension.wasm",
            )

    def test_skip_test_does_not_execute_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.environment(directory)
            env["CI_SKIP_TESTS"] = "true"
            runner = RecordingRunner(env)
            runner.test()
            self.assertEqual(runner.commands, [])

    def test_linux_test_runs_inside_and_outside_docker(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = RecordingRunner(self.environment(directory))
            runner.test()
            self.assertEqual(len(runner.commands), 2)
            self.assertEqual(runner.commands[0][0][-2:], ["make", "test_release"])
            self.assertEqual(runner.commands[1][0], ["make", "test_release"])

    def test_linux_build_uses_action_cache_directory_and_five_gigabyte_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.environment(directory)
            env.update(
                {
                    "CI_VCPKG_URL": "https://github.com/microsoft/vcpkg.git",
                    "CI_VCPKG_COMMIT": "abc123",
                    "CI_VCPKG_OVERLAY_PORTS": "extension-ci-tools/vcpkg_ports",
                    "CI_VCPKG_OVERLAY_TRIPLETS": "extension-ci-tools/toolchains",
                    "CI_CUDA_VERSION": "13",
                }
            )
            runner = RecordingRunner(env)
            runner.build_linux()
            docker_environment = Path(directory, "docker_env.txt").read_text(encoding="utf-8")
            self.assertIn("CCACHE_MAXSIZE=5G\n", docker_environment)
            configure = runner.commands[-2][0]
            self.assertIn(f"{Path(directory).resolve() / '.ccache'}:/ccache_dir", configure)

    def test_windows_build_separates_optional_linker_rename(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.environment(directory, "windows", "windows_amd64")
            runner = RecordingRunner(env)
            runner.build_windows()

            self.assertEqual(len(runner.commands), 2)
            rename_command, rename_options = runner.commands[0]
            self.assertIsInstance(rename_command, str)
            self.assertTrue(rename_options["shell"])
            self.assertIn(
                'if exist "C:\\Program Files\\Git\\usr\\bin\\link.exe" move',
                rename_command,
            )

            build_command, build_options = runner.commands[1]
            self.assertIsInstance(build_command, str)
            self.assertTrue(build_options["shell"])
            self.assertIn(
                'call "C:\\Program Files\\Microsoft Visual Studio\\18\\Enterprise'
                '\\VC\\Auxiliary\\Build\\vcvars64.bat"',
                build_command,
            )
            self.assertNotIn('\\"', build_command)
            self.assertNotIn("link.exe", build_command)
            self.assertTrue(build_command.endswith(" && make release"))

    def test_upload_writes_outputs_and_validates_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            artifact = workspace / "build/release/extension/quack/quack.duckdb_extension"
            artifact.parent.mkdir(parents=True)
            artifact.touch()
            output = workspace / "github-output"
            env = self.environment(workspace)
            env["GITHUB_OUTPUT"] = str(output)
            runner = RecordingRunner(env)
            runner.upload()
            values = output.read_text(encoding="utf-8")
            self.assertIn("artifact_path=build/release/extension/quack/quack.duckdb_extension", values)
            self.assertIn("artifact_name=quack-v1.2.3-extension-linux_amd64", values)

    def test_upload_fails_when_artifact_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = RecordingRunner(self.environment(directory))
            with self.assertRaises(FileNotFoundError):
                runner.upload()


if __name__ == "__main__":
    unittest.main()
