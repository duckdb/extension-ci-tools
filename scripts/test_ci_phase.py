from contextlib import redirect_stderr
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parent))

from ci_phase import (  # noqa: E402
    PhaseRunner,
    extra_dependencies,
    format_windows_command,
    main,
    is_true,
    test_environment,
    tool_enabled,
)


class RecordingRunner(PhaseRunner):
    def __init__(self, environ):
        super().__init__(environ)
        self.commands = []
        self.captured_output = ""

    def run(self, command, **kwargs):
        self.commands.append((command, kwargs))

    def run_with_retry(self, command, **kwargs):
        self.commands.append((command, {**kwargs, "retry": True}))

    def capture(self, command, **kwargs):
        self.commands.append((command, {**kwargs, "capture": True}))
        return self.captured_output


class CIPhaseTest(unittest.TestCase):
    def environment(self, workspace, platform="linux", architecture="linux_amd64"):
        return {
            "CI_PLATFORM": platform,
            "DUCKDB_PLATFORM": architecture,
            "GITHUB_WORKSPACE": str(workspace),
            "CI_EXTENSION_NAME": "quack",
            "CI_EXTENSION_CANONICAL": "",
            "DUCKDB_VERSION": "v1.2.3",
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

    def test_windows_command_uses_cmd_compatible_quoting(self):
        command = format_windows_command(
            [
                r"C:\Program Files\Python\python.exe",
                r"D:\a\duckdb\duckdb\scripts\ci\retry.py",
                "--",
                "make",
                "release",
            ]
        )

        self.assertEqual(
            command,
            '"C:\\Program Files\\Python\\python.exe" '
            "D:\\a\\duckdb\\duckdb\\scripts\\ci\\retry.py -- make release",
        )
        self.assertNotIn("'", command)

    def test_intel_macos_dependencies_use_pinned_homebrew_installer(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = self.environment(directory, "macos", "osx_amd64")
            installer = (
                "https://raw.githubusercontent.com/Homebrew/install/"
                "0f5b7666a65fc2d1a2615549f02771353c250f9a/install.sh"
            )

            omp = RecordingRunner(environment)
            omp.setup_macos_omp()
            self.assertEqual(
                omp.commands[0],
                (
                    f'arch -x86_64 /bin/bash -c "$(curl -fsSL {installer})"',
                    {"shell": True},
                ),
            )
            self.assertEqual(
                omp.commands[1][0],
                ["arch", "-x86_64", "/usr/local/bin/brew", "install", "libomp"],
            )

            unixodbc = RecordingRunner(environment)
            unixodbc.setup_macos_unixodbc()
            self.assertEqual(
                unixodbc.commands[0],
                (
                    f'arch -x86_64 /bin/bash -c "$(curl -fsSL {installer})"',
                    {"shell": True},
                ),
            )
            self.assertEqual(
                [command for command, _ in unixodbc.commands[1:]],
                [
                    ["/usr/local/bin/brew", "config"],
                    ["/usr/local/bin/brew", "install", "unixodbc"],
                    ["/usr/local/bin/brew", "ls", "-v", "unixodbc"],
                ],
            )

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

    def test_duckdb_build_identity_is_forwarded(self):
        with tempfile.TemporaryDirectory() as directory:
            commit = "0123456789abcdef0123456789abcdef01234567"
            env = self.environment(directory)
            env.update(
                {
                    "DUCKDB_VERSION": "v2.0.0-alpha39940",
                    "DUCKDB_COMMIT": commit,
                }
            )
            runner = RecordingRunner(env)

            self.assertEqual(runner.duckdb_ref(), commit)
            self.assertEqual(runner.env["DUCKDB_VERSION"], "v2.0.0-alpha39940")
            self.assertEqual(runner.env["DUCKDB_COMMIT"], commit)
            native_environment = runner.build_environment()
            self.assertEqual(native_environment["DUCKDB_VERSION"], "v2.0.0-alpha39940")
            self.assertEqual(native_environment["DUCKDB_COMMIT"], commit)

            runner.create_docker_environment()
            docker_environment = Path(directory, "docker_env.txt").read_text(
                encoding="utf-8"
            )
            self.assertIn("DUCKDB_VERSION=v2.0.0-alpha39940\n", docker_environment)
            self.assertIn(f"DUCKDB_COMMIT={commit}\n", docker_environment)

    def test_duckdb_ref_falls_back_to_version(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = RecordingRunner(self.environment(directory))
            self.assertEqual(runner.duckdb_ref(), "v1.2.3")

    def test_checkout_runs_only_requested_operations(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.environment(directory)
            env.update(
                {
                    "CI_DUCKDB_GIT_REPOSITORY": "duckdb/duckdb-fork",
                    "CI_EXTENSION_TAG": "v2.0.0",
                    "DUCKDB_TAG": "v1.2.3-test",
                }
            )
            runner = RecordingRunner(env)
            runner.checkout()
            commands = [command for command, _ in runner.commands]
            self.assertEqual(
                commands,
                [
                    ["git", "clone", "duckdb/duckdb-fork", "duckdb"],
                    ["git", "-C", "duckdb", "fetch", "origin", "v1.2.3"],
                    ["git", "-C", "duckdb", "checkout", "v1.2.3"],
                    ["git", "tag", "v2.0.0"],
                    ["make", "set_duckdb_tag"],
                ],
            )
            self.assertEqual(
                runner.commands[-1][1]["extra_env"],
                {"DUCKDB_TAG": "v1.2.3-test"},
            )

    def test_checkout_clones_default_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.environment(directory)
            runner = RecordingRunner(env)
            runner.checkout()
            commands = [command for command, _ in runner.commands]
            self.assertEqual(
                commands,
                [
                    [
                        "git",
                        "clone",
                        "https://github.com/duckdb/duckdb.git",
                        "duckdb",
                    ],
                    ["git", "-C", "duckdb", "fetch", "origin", "v1.2.3"],
                    ["git", "-C", "duckdb", "checkout", "v1.2.3"],
                ],
            )

    def test_checkout_reuses_existing_duckdb_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "duckdb").mkdir()
            env = self.environment(workspace)
            env["CI_DUCKDB_GIT_REPOSITORY"] = "https://github.com/duckdb/duckdb.git"
            runner = RecordingRunner(env)
            runner.checkout()
            commands = [command for command, _ in runner.commands]
            self.assertEqual(
                commands,
                [
                    [
                        "git",
                        "-C",
                        "duckdb",
                        "remote",
                        "set-url",
                        "origin",
                        "https://github.com/duckdb/duckdb.git",
                    ],
                    ["git", "-C", "duckdb", "fetch", "origin", "v1.2.3"],
                    ["git", "-C", "duckdb", "checkout", "v1.2.3"],
                ],
            )

    def test_checkout_resolves_existing_duckdb_when_version_is_omitted(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "duckdb").mkdir()
            github_environment = workspace / "github-environment"
            github_output = workspace / "github-output"
            env = self.environment(workspace)
            env.update(
                {
                    "DUCKDB_VERSION": "",
                    "GITHUB_ENV": str(github_environment),
                    "GITHUB_OUTPUT": str(github_output),
                }
            )
            sha = "0123456789abcdef0123456789abcdef01234567"
            runner = RecordingRunner(env)
            runner.captured_output = sha

            runner.checkout()

            self.assertEqual(
                [command for command, _ in runner.commands],
                [["git", "-C", "duckdb", "rev-parse", "HEAD"]],
            )
            self.assertEqual(runner.env["DUCKDB_VERSION"], sha)
            self.assertEqual(
                github_environment.read_text(encoding="utf-8"),
                f"DUCKDB_VERSION={sha}\n",
            )
            self.assertEqual(
                github_output.read_text(encoding="utf-8"),
                f"duckdb_version={sha}\n",
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

    def create_prebuilt_archive(self, root, artifact_name, members):
        archive_path = root / artifact_name
        root.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive_path, "w:gz") as archive:
            for name, contents in members:
                member = tarfile.TarInfo(name)
                member.size = len(contents)
                archive.addfile(member, io.BytesIO(contents))
        return archive_path

    def test_prebuilt_duckdb_archive_is_forwarded_to_build(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            artifact_name = "duckdb-static-libs-linux-amd64.tar.gz"
            artifact_root = workspace / ".ci" / "prebuilt-duckdb" / "linux_amd64"
            self.create_prebuilt_archive(
                artifact_root,
                artifact_name,
                [
                    ("libduckdb_static.a", b"library"),
                    ("libduckdb_shell.a", b"shell"),
                    ("libcore_functions_extension.a", b"core functions"),
                    ("nested/libparquet_extension.a", b"parquet"),
                    ("README", b"ignored"),
                ],
            )
            env = self.environment(workspace)
            env.update(
                {
                    "CI_LINUX_NATIVE_CONTAINER": "true",
                    "CI_PREBUILT_DUCKDB_ARTIFACT": artifact_name,
                    "CI_PREBUILT_DUCKDB_PATH": str(artifact_root),
                    "CI_BUILD_DUCKDB_SHELL": "true",
                }
            )
            runner = RecordingRunner(env)
            runner.build()

            library = artifact_root / "extracted" / "libduckdb_static.a"
            self.assertEqual(library.read_bytes(), b"library")
            self.assertEqual(
                runner.env["DUCKDB_PREBUILT_LIBRARY"], str(library.resolve())
            )
            self.assertEqual(
                runner.commands[0][1]["extra_env"]["DUCKDB_PREBUILT_LIBRARY"],
                str(library.resolve()),
            )
            self.assertEqual(
                runner.env["DUCKDB_PREBUILT_EXTENSIONS"],
                "core_functions;parquet",
            )
            self.assertEqual(
                runner.commands[0][1]["extra_env"]["DUCKDB_PREBUILT_EXTENSIONS"],
                "core_functions;parquet",
            )
            self.assertEqual(
                (artifact_root / "extracted" / "libduckdb_shell.a").read_bytes(),
                b"shell",
            )
            self.assertEqual(
                (
                    artifact_root
                    / "extracted"
                    / "libcore_functions_extension.a"
                ).read_bytes(),
                b"core functions",
            )
            self.assertEqual(
                (artifact_root / "extracted" / "libparquet_extension.a").read_bytes(),
                b"parquet",
            )
            self.assertFalse((artifact_root / "extracted" / "README").exists())

    def test_prebuilt_duckdb_uses_msvc_library_name(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            artifact_name = "duckdb-static-libs-windows-amd64.tar.gz"
            artifact_root = workspace / ".ci" / "prebuilt-duckdb" / "windows_amd64"
            self.create_prebuilt_archive(
                artifact_root,
                artifact_name,
                [
                    ("duckdb_static.lib", b"library"),
                    ("duckdb_shell.lib", b"shell"),
                    ("core_functions_extension.lib", b"core functions"),
                    ("parquet_extension.lib", b"parquet"),
                ],
            )
            env = self.environment(workspace, "windows", "windows_amd64")
            env.update(
                {
                    "CI_PREBUILT_DUCKDB_ARTIFACT": artifact_name,
                    "CI_PREBUILT_DUCKDB_PATH": str(artifact_root),
                    "CI_BUILD_DUCKDB_SHELL": "true",
                }
            )
            runner = RecordingRunner(env)
            runner.prepare_prebuilt_duckdb()

            library = artifact_root / "extracted" / "duckdb_static.lib"
            self.assertEqual(library.read_bytes(), b"library")
            self.assertEqual(
                runner.env["DUCKDB_PREBUILT_LIBRARY"], str(library.resolve())
            )
            self.assertEqual(
                runner.env["DUCKDB_PREBUILT_EXTENSIONS"],
                "core_functions;parquet",
            )
            self.assertEqual(
                (artifact_root / "extracted" / "duckdb_shell.lib").read_bytes(),
                b"shell",
            )

    def test_prebuilt_duckdb_requires_shell_library_when_building_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            artifact_name = "duckdb-static-libs-linux-amd64.tar.gz"
            artifact_root = workspace / ".ci" / "prebuilt-duckdb" / "linux_amd64"
            env = self.environment(workspace)
            env.update(
                {
                    "CI_PREBUILT_DUCKDB_ARTIFACT": artifact_name,
                    "CI_PREBUILT_DUCKDB_PATH": str(artifact_root),
                    "CI_BUILD_DUCKDB_SHELL": "true",
                }
            )

            self.create_prebuilt_archive(
                artifact_root, artifact_name, [("libduckdb_static.a", b"library")]
            )
            with self.assertRaisesRegex(ValueError, "expected one libduckdb_shell.a"):
                RecordingRunner(env).prepare_prebuilt_duckdb()

            self.create_prebuilt_archive(
                artifact_root,
                artifact_name,
                [
                    ("libduckdb_static.a", b"library"),
                    ("libduckdb_shell.a", b"one"),
                    ("nested/libduckdb_shell.a", b"two"),
                ],
            )
            with self.assertRaisesRegex(ValueError, "expected one libduckdb_shell.a"):
                RecordingRunner(env).prepare_prebuilt_duckdb()

    def test_prebuilt_duckdb_rejects_missing_or_duplicate_library(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            artifact_name = "duckdb-static-libs-linux-amd64.tar.gz"
            artifact_root = workspace / ".ci" / "prebuilt-duckdb" / "linux_amd64"
            env = self.environment(workspace)
            env.update(
                {
                    "CI_PREBUILT_DUCKDB_ARTIFACT": artifact_name,
                    "CI_PREBUILT_DUCKDB_PATH": str(artifact_root),
                }
            )

            with self.assertRaisesRegex(FileNotFoundError, "found 0"):
                RecordingRunner(env).prepare_prebuilt_duckdb()

            self.create_prebuilt_archive(
                artifact_root, artifact_name, [("duckdb.h", b"header")]
            )
            with self.assertRaisesRegex(ValueError, "expected one libduckdb_static.a"):
                RecordingRunner(env).prepare_prebuilt_duckdb()

            self.create_prebuilt_archive(
                artifact_root,
                artifact_name,
                [("libduckdb_static.a", b"one"), ("nested/libduckdb_static.a", b"two")],
            )
            with self.assertRaisesRegex(ValueError, "found 2"):
                RecordingRunner(env).prepare_prebuilt_duckdb()

            self.create_prebuilt_archive(
                artifact_root,
                artifact_name,
                [
                    ("libduckdb_static.a", b"library"),
                    ("libparquet_extension.a", b"one"),
                    ("nested/libparquet_extension.a", b"two"),
                ],
            )
            with self.assertRaisesRegex(ValueError, "duplicate prebuilt library"):
                RecordingRunner(env).prepare_prebuilt_duckdb()

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

    def test_linux_native_container_build_and_test_do_not_use_docker(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.environment(directory)
            env["CI_LINUX_NATIVE_CONTAINER"] = "true"
            runner = RecordingRunner(env)
            runner.setup_linux()
            runner.build_linux()
            runner.test()
            self.assertEqual(runner.commands[0][0], ["make", "configure_ci"])
            self.assertEqual(runner.commands[1][0][-2:], ["make", "release"])
            self.assertEqual(runner.commands[2][0], ["make", "test_release"])
            self.assertNotIn("docker", str(runner.commands))

    def test_extension_config_paths_are_resolved_from_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "extension_config.cmake").write_text("set(BASE 1)\n", encoding="utf-8")
            config = workspace / "config" / "group.cmake"
            config.parent.mkdir()
            config.write_text("set(GROUP 1)\n", encoding="utf-8")
            env = self.environment(workspace)
            env["CI_EXTENSION_CONFIG_PATHS"] = '["config/group.cmake"]'
            runner = RecordingRunner(env)
            runner.inject_extension_config()
            contents = (workspace / "extension_config.cmake").read_text(encoding="utf-8")
            self.assertIn("set(BASE 1)", contents)
            self.assertIn("set(GROUP 1)", contents)

    def test_missing_extension_config_path_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.environment(directory)
            env["CI_EXTENSION_CONFIG_PATHS"] = '["missing.cmake"]'
            runner = RecordingRunner(env)
            with self.assertRaises(FileNotFoundError):
                runner.inject_extension_config()

    def test_failed_phase_command_exits_concisely_for_list_and_shell_commands(self):
        commands = (
            (["make", "test release"], "make 'test release'"),
            ("make test_release", "make test_release"),
        )
        with tempfile.TemporaryDirectory() as directory:
            environment = self.environment(directory)
            for command, printable in commands:
                with self.subTest(command=command):
                    error = subprocess.CalledProcessError(2, command)
                    stderr = io.StringIO()
                    with (
                        mock.patch.dict(os.environ, environment, clear=True),
                        mock.patch.object(sys, "argv", ["ci_phase.py", "test"]),
                        mock.patch.object(PhaseRunner, "test", side_effect=error),
                        redirect_stderr(stderr),
                    ):
                        with self.assertRaises(SystemExit) as raised:
                            main()

                    self.assertEqual(raised.exception.code, 2)
                    self.assertEqual(
                        stderr.getvalue(),
                        f"error: command failed with exit code 2: {printable}\n",
                    )
                    self.assertNotIn("Traceback", stderr.getvalue())

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

    def test_windows_build_selects_vcvars_before_running_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.environment(directory, "windows", "windows_amd64")
            env["VCPKG_TARGET_TRIPLET"] = "x64-windows-static-release"
            retry_script = Path(directory, "duckdb", "scripts", "ci", "retry.py").resolve()
            retry_script.parent.mkdir(parents=True)
            retry_script.touch()
            for has_vs18, version in ((True, "18"), (False, "2022")):
                with self.subTest(has_vs18=has_vs18):
                    runner = RecordingRunner(env)
                    with mock.patch(
                        "ci_phase.os.path.isfile",
                        side_effect=lambda path: Path(path) == retry_script or has_vs18,
                    ):
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
                        f'call "C:\\Program Files\\Microsoft Visual Studio\\{version}'
                        '\\Enterprise\\VC\\Auxiliary\\Build\\vcvars64.bat"',
                        build_command,
                    )
                    self.assertNotIn('\\"', build_command)
                    self.assertNotIn("if exist", build_command)
                    self.assertNotIn(" else ", build_command)
                    self.assertNotIn("link.exe", build_command)
                    self.assertIn(str(retry_script), build_command)
                    self.assertTrue(build_command.endswith(" -- make release"))
                    self.assertIn(
                        "-DVCPKG_APPLOCAL_DEPS=OFF",
                        build_options["extra_env"]["EXT_FLAGS"],
                    )

    def test_windows_disables_vcpkg_applocal_only_for_static_triplets(self):
        with tempfile.TemporaryDirectory() as directory:
            cases = (
                ("windows_amd64", "x64-windows-static-release", True),
                ("windows_arm64", "arm64-windows-static-release", True),
                ("windows_amd64_mingw", "x64-mingw-static", True),
                ("windows_amd64", "x64-windows", False),
            )
            for architecture, triplet, expected in cases:
                with self.subTest(architecture=architecture, triplet=triplet):
                    env = self.environment(directory, "windows", architecture)
                    env["VCPKG_TARGET_TRIPLET"] = triplet
                    runner = RecordingRunner(env)
                    with mock.patch("ci_phase.os.path.isfile", return_value=False):
                        runner.build_windows()

                    extension_flags = runner.commands[-1][1]["extra_env"]["EXT_FLAGS"]
                    self.assertEqual(
                        "-DVCPKG_APPLOCAL_DEPS=OFF" in extension_flags,
                        expected,
                    )
                    self.assertIn("-DCMAKE_C_COMPILER_LAUNCHER=ccache", extension_flags)
                    self.assertIn("-DCMAKE_CXX_COMPILER_LAUNCHER=ccache", extension_flags)

    def test_upload_writes_outputs_and_validates_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            artifact = workspace / "build/release/extension/quack/quack.duckdb_extension"
            artifact.parent.mkdir(parents=True)
            artifact.touch()
            output = workspace / "github-output"
            env = self.environment(workspace)
            env["CI_SKIP_TESTS"] = "true"
            env["GITHUB_OUTPUT"] = str(output)
            runner = RecordingRunner(env)
            runner.upload()
            values = output.read_text(encoding="utf-8")
            artifact_path = Path(
                next(
                    line.removeprefix("artifact_path=")
                    for line in values.splitlines()
                    if line.startswith("artifact_path=")
                )
            )
            self.assertEqual(artifact_path.parent, workspace.resolve() / "ci-artifacts")
            self.assertTrue((artifact_path / artifact.name).is_file())
            self.assertFalse((artifact_path / "test-support").exists())
            self.assertIn("artifact_name=quack-v1.2.3-extension-linux_amd64", values)

    def test_upload_fails_when_artifact_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = RecordingRunner(self.environment(directory))
            with self.assertRaises(FileNotFoundError):
                runner.upload()

    def test_upload_requires_duckdb_version(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            artifact = workspace / "build/release/extension/quack/quack.duckdb_extension"
            artifact.parent.mkdir(parents=True)
            artifact.touch()
            env = self.environment(workspace)
            env["DUCKDB_VERSION"] = ""

            with self.assertRaisesRegex(
                ValueError, "missing required environment variable: DUCKDB_VERSION"
            ):
                RecordingRunner(env).upload()

    def test_upload_allows_missing_unittest_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            artifact = workspace / "build/release/extension/quack/quack.duckdb_extension"
            artifact.parent.mkdir(parents=True)
            artifact.touch()
            output = workspace / "github-output"
            env = self.environment(workspace)
            env["GITHUB_OUTPUT"] = str(output)
            RecordingRunner(env).upload()

            values = output.read_text(encoding="utf-8")
            artifact_path = Path(
                next(
                    line.removeprefix("artifact_path=")
                    for line in values.splitlines()
                    if line.startswith("artifact_path=")
                )
            )
            archives = list(artifact_path.glob("test-support/**/test-support.tar.gz"))
            self.assertEqual(len(archives), 1)
            with tarfile.open(archives[0], "r:gz") as bundle:
                self.assertFalse(
                    any(Path(name).name.startswith("unittest") for name in bundle.getnames())
                )

    def test_upload_and_test_restore_embedded_group_support(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            build = workspace / "build" / "release"
            unittest_binary = build / "test" / ("unittest.exe" if os.name == "nt" else "unittest")
            unittest_binary.parent.mkdir(parents=True)
            unittest_binary.write_text("runner", encoding="utf-8")
            unittest_binary.chmod(0o755)
            repository = build / "repository" / "v1" / "linux_amd64"
            repository.mkdir(parents=True)
            (repository / "quack.duckdb_extension").write_text("extension", encoding="utf-8")

            env = self.environment(workspace)
            env["CI_UPLOAD_ALL_EXTENSIONS"] = "true"
            downloaded = workspace / "downloaded-repository"
            for group in ("group-one", "group-two"):
                output = workspace / f"github-output-{group}"
                env["CI_ARTIFACT_NAME"] = group
                env["GITHUB_OUTPUT"] = str(output)
                PhaseRunner(env).upload()
                values = output.read_text(encoding="utf-8")
                artifact_path = Path(
                    next(
                        line.removeprefix("artifact_path=")
                        for line in values.splitlines()
                        if line.startswith("artifact_path=")
                    )
                )
                shutil.copytree(artifact_path, downloaded, dirs_exist_ok=True)

            archives = sorted(downloaded.glob("test-support/**/test-support.tar.gz"))
            self.assertEqual(len(archives), 2)
            archive = archives[0]
            with tarfile.open(archive, "r:gz") as bundle:
                names = bundle.getnames()
                unittest_member = bundle.getmember(f"release/test/{unittest_binary.name}")
            self.assertIn(f"release/test/{unittest_binary.name}", names)
            self.assertFalse(any("repository" in name for name in names))
            if os.name != "nt":
                self.assertNotEqual(unittest_member.mode & 0o111, 0)

            env["CI_EXTENSION_ARTIFACT_DIR"] = str(downloaded)
            restored = RecordingRunner(env)
            restored.test()
            self.assertTrue(
                (build / "repository" / "v1" / "linux_amd64" / "quack.duckdb_extension").is_file()
            )
            self.assertFalse((build / "repository" / "test-support").exists())
            self.assertEqual(restored.commands[0][0][-2:], ["make", "test_release"])
            self.assertEqual(len(restored.commands), 4)

    def test_support_is_enabled_only_for_testable_platforms(self):
        with tempfile.TemporaryDirectory() as directory:
            cases = (
                (self.environment(directory), True),
                (self.environment(directory, "linux", "linux_arm64"), False),
                (self.environment(directory, "wasm", "wasm_eh"), False),
                (self.environment(directory, "windows", "windows_amd64"), True),
            )
            macos_env = self.environment(directory, "macos", "osx_amd64")
            macos_env["CI_OSX_BUILD_ARCH"] = "x86_64"
            cases += ((macos_env, False),)
            native_macos_env = self.environment(directory, "macos", "osx_arm64")
            native_macos_env["CI_OSX_BUILD_ARCH"] = "arm64"
            cases += ((native_macos_env, True),)

            for env, expected in cases:
                with self.subTest(
                    platform=env["CI_PLATFORM"],
                    architecture=env["DUCKDB_PLATFORM"],
                ):
                    self.assertEqual(
                        RecordingRunner(env)._test_support_enabled(), expected
                    )


if __name__ == "__main__":
    unittest.main()
