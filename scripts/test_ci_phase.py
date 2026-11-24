from contextlib import redirect_stderr, redirect_stdout
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
import urllib.error


sys.path.insert(0, str(Path(__file__).resolve().parent))

from ci_phase import (  # noqa: E402
    PhaseRunner,
    duckdb_cli_artifact_name,
    duckdb_test_artifact_name,
    duckdb_release_line,
    extra_dependencies,
    fetch_duckdb_tags,
    format_windows_command,
    highest_duckdb_version_tag,
    main,
    is_true,
    prebuilt_duckdb_artifact_name,
    resolve_duckdb_version,
    staged_duckdb_version,
    tags_at_commit,
    test_environment,
    tool_enabled,
)


class RecordingRunner(PhaseRunner):
    def __init__(self, environ):
        super().__init__(environ)
        self.commands = []
        self.captured_output = ""
        self.captured_outputs = []

    def run(self, command, **kwargs):
        self.commands.append((command, kwargs))

    def run_with_retry(self, command, **kwargs):
        self.commands.append((command, {**kwargs, "retry": True}))

    def capture(self, command, **kwargs):
        self.commands.append((command, {**kwargs, "capture": True}))
        if self.captured_outputs:
            return self.captured_outputs.pop(0)
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
            "CI_FETCH_PREBUILT_DUCKDB": "true",
            "CI_TEST_CONFIG": "{}",
            "CI_EXTENSIONS_TEST_SELECTION": "regular",
        }

    def test_boolean_and_toolchain_parsing(self):
        self.assertTrue(is_true("true"))
        self.assertTrue(is_true("1"))
        self.assertFalse(is_true("false"))
        self.assertTrue(tool_enabled("rust;go", "go"))
        self.assertFalse(tool_enabled("fortran", "go"))

    def test_build_directory_selects_nested_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            build_directory = workspace / "nested" / "extension"
            build_directory.mkdir(parents=True)
            environment = self.environment(workspace)
            environment["CI_BUILD_DIRECTORY"] = "nested/extension"

            runner = RecordingRunner(environment)

            self.assertEqual(runner.workspace, build_directory.resolve())

    def test_nested_build_directory_preserves_repository_root_in_docker(self):
        with tempfile.TemporaryDirectory() as directory:
            repository_root = Path(directory)
            build_directory = repository_root / "nested" / "extension"
            build_directory.mkdir(parents=True)
            environment = self.environment(repository_root)
            environment["CI_BUILD_DIRECTORY"] = "nested/extension"

            runner = RecordingRunner(environment)

            self.assertEqual(
                runner.docker_arguments(),
                [
                    "docker",
                    "run",
                    "--env-file=docker_env.txt",
                    "-v",
                    f"{repository_root.resolve()}:/duckdb_build_dir",
                    "-v",
                    f"{build_directory.resolve() / '.ccache'}:/ccache_dir",
                    "--workdir",
                    "/duckdb_build_dir/nested/extension",
                    "duckdb/linux_amd64",
                ],
            )
            self.assertEqual(
                runner.docker_path("extension-ci-tools/vcpkg_ports"),
                "/duckdb_build_dir/nested/extension/extension-ci-tools/vcpkg_ports",
            )

    def test_build_directory_must_stay_in_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            environment = self.environment(workspace)

            environment["CI_BUILD_DIRECTORY"] = str(workspace)
            with self.assertRaisesRegex(ValueError, "must be relative"):
                RecordingRunner(environment)

            environment["CI_BUILD_DIRECTORY"] = "../outside"
            with self.assertRaisesRegex(ValueError, "must stay within"):
                RecordingRunner(environment)

    def test_highest_duckdb_version_tag(self):
        self.assertEqual(
            highest_duckdb_version_tag(
                [
                    "not-a-version",
                    "v2.0.0-dev99",
                    "v2.0.0-alpha1",
                    "v2.0.0-rc3",
                    "v2.0.0",
                    "v1.99.99",
                ]
            ),
            "v2.0.0",
        )
        self.assertEqual(
            highest_duckdb_version_tag(
                ["v2.0.0-dev100", "v2.0.0-alpha1", "v2.0.0-rc1"]
            ),
            "v2.0.0-rc1",
        )
        self.assertEqual(
            highest_duckdb_version_tag(["v2.0.0-rc2", "v2.0.0-rc10"]),
            "v2.0.0-rc10",
        )
        self.assertEqual(
            highest_duckdb_version_tag(["release-v2.0.0", "v2.0"]),
            "",
        )

    def test_duckdb_release_line(self):
        for version in (
            "v2.1.0",
            "v2.1.0-alpha51401",
            "v2.1.0-rc1",
            "v2.1.0-dev42",
        ):
            with self.subTest(version=version):
                self.assertEqual(duckdb_release_line(version), "2.1")
        self.assertEqual(
            duckdb_release_line("5bf940c1318234d360aa98d476fbcd7762997c2f"),
            "unversioned",
        )

    def test_staged_duckdb_version(self):
        commit = "5bf940c1318234d360aa98d476fbcd7762997c2f"
        open_url = mock.Mock(return_value=io.BytesIO(b"v2.0.0-alpha48877\n"))

        version = staged_duckdb_version(
            commit, "https://staging.example/", open_url
        )

        self.assertEqual(version, "v2.0.0-alpha48877")
        open_url.assert_called_once_with(
            "https://staging.example/5bf940c131/latest_alpha_version.txt"
        )

    def test_tags_at_commit_includes_lightweight_and_annotated_tags(self):
        commit = "5bf940c1318234d360aa98d476fbcd7762997c2f"
        remote_tags = "\n".join(
            [
                f"{commit}\trefs/tags/v2.0.0",
                "0123456789012345678901234567890123456789\trefs/tags/v2.0.0-rc1",
                f"{commit}\trefs/tags/v2.0.0-rc1^{{}}",
                f"{commit}\trefs/heads/main",
            ]
        )

        self.assertEqual(
            tags_at_commit(remote_tags, commit),
            ["v2.0.0", "v2.0.0-rc1"],
        )

    @mock.patch("ci_phase.subprocess.run")
    def test_fetch_duckdb_tags_uses_github_repository_name(self, run):
        commit = "5bf940c1318234d360aa98d476fbcd7762997c2f"
        run.return_value.stdout = f"{commit}\trefs/tags/v2.0.0\n"

        self.assertEqual(fetch_duckdb_tags("duckdb/duckdb", commit), ["v2.0.0"])
        run.assert_called_once_with(
            [
                "git",
                "ls-remote",
                "--tags",
                "https://github.com/duckdb/duckdb.git",
            ],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )

    def test_resolve_duckdb_version_prefers_highest_version_tag(self):
        commit = "5bf940c1318234d360aa98d476fbcd7762997c2f"
        open_url = mock.Mock()

        self.assertEqual(
            resolve_duckdb_version(
                "",
                commit,
                ["v2.0.0-rc1", "not-a-version", "v2.0.0"],
                open_url=open_url,
            ),
            "v2.0.0",
        )
        open_url.assert_not_called()

    def test_resolve_duckdb_version_uses_staged_version_for_commit(self):
        commit = "5bf940c1318234d360aa98d476fbcd7762997c2f"
        open_url = mock.Mock(return_value=io.BytesIO(b"v2.0.0-alpha48877\n"))

        self.assertEqual(
            resolve_duckdb_version("", commit, open_url=open_url),
            "v2.0.0-alpha48877",
        )

    def test_resolve_duckdb_version_preserves_explicit_version(self):
        open_url = mock.Mock()

        self.assertEqual(
            resolve_duckdb_version(
                "v2.0.0",
                "5bf940c1318234d360aa98d476fbcd7762997c2f",
                open_url=open_url,
            ),
            "v2.0.0",
        )
        open_url.assert_not_called()

    def test_resolve_duckdb_version_falls_back_when_pointer_is_missing(self):
        commit = "5bf940c1318234d360aa98d476fbcd7762997c2f"
        missing = urllib.error.HTTPError(
            "https://staging.example/pointer", 404, "Not Found", {}, None
        )
        output = io.StringIO()

        with redirect_stdout(output):
            version = resolve_duckdb_version(
                "", commit, open_url=mock.Mock(side_effect=missing)
            )
        missing.close()

        self.assertEqual(version, commit)
        self.assertIn("Could not resolve staged DuckDB version", output.getvalue())

    def test_resolve_version_phase_writes_staged_version_output(self):
        commit = "5bf940c1318234d360aa98d476fbcd7762997c2f"
        with tempfile.TemporaryDirectory() as directory:
            github_output = Path(directory) / "github-output"
            environment = {
                "DUCKDB_VERSION": "",
                "DUCKDB_COMMIT": commit,
                "GITHUB_OUTPUT": str(github_output),
            }
            with (
                mock.patch.dict(os.environ, environment, clear=True),
                mock.patch.object(sys, "argv", ["ci_phase.py", "resolve-version"]),
                mock.patch("ci_phase.fetch_duckdb_tags", return_value=[]),
                mock.patch(
                    "ci_phase.open_prebuilt_url",
                    return_value=io.BytesIO(b"v2.0.0-alpha48877\n"),
                ),
            ):
                main()

            self.assertEqual(
                github_output.read_text(encoding="utf-8"),
                "version=v2.0.0-alpha48877\nrelease_line=2.0\n",
            )

    def test_resolve_version_phase_prefers_tag_over_staged_version(self):
        commit = "5bf940c1318234d360aa98d476fbcd7762997c2f"
        with tempfile.TemporaryDirectory() as directory:
            github_output = Path(directory) / "github-output"
            environment = {
                "DUCKDB_VERSION": "",
                "DUCKDB_COMMIT": commit,
                "GITHUB_OUTPUT": str(github_output),
            }
            with (
                mock.patch.dict(os.environ, environment, clear=True),
                mock.patch.object(sys, "argv", ["ci_phase.py", "resolve-version"]),
                mock.patch(
                    "ci_phase.fetch_duckdb_tags",
                    return_value=["v2.0.0-rc1", "v2.0.0"],
                ),
                mock.patch("ci_phase.open_prebuilt_url") as open_url,
            ):
                main()

            self.assertEqual(
                github_output.read_text(encoding="utf-8"),
                "version=v2.0.0\nrelease_line=2.0\n",
            )
            open_url.assert_not_called()

    def test_staged_duckdb_version_rejects_invalid_pointer(self):
        commit = "5bf940c1318234d360aa98d476fbcd7762997c2f"

        with self.assertRaisesRegex(ValueError, "invalid staged DuckDB version"):
            staged_duckdb_version(
                commit,
                open_url=mock.Mock(return_value=io.BytesIO(b"../../unexpected\n")),
            )

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
            runner.prebuilt = mock.Mock()
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
            runner.prebuilt.assert_called_once_with()

    def test_checkout_does_not_fetch_prebuilt_without_distribution_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.environment(directory)
            del env["CI_FETCH_PREBUILT_DUCKDB"]
            runner = RecordingRunner(env)
            runner.prebuilt = mock.Mock()

            runner.checkout()

            runner.prebuilt.assert_not_called()

    def test_checkout_clones_default_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.environment(directory)
            runner = RecordingRunner(env)
            runner.prebuilt = mock.Mock()
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
            runner.prebuilt.assert_called_once_with()

    def test_checkout_reuses_existing_duckdb_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "duckdb").mkdir()
            env = self.environment(workspace)
            env["CI_DUCKDB_GIT_REPOSITORY"] = "https://github.com/duckdb/duckdb.git"
            runner = RecordingRunner(env)
            runner.prebuilt = mock.Mock()
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
            runner.prebuilt.assert_called_once_with()

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
            runner.captured_outputs = ["", sha]
            runner.prebuilt = mock.Mock()

            output = io.StringIO()
            with redirect_stdout(output):
                runner.checkout()

            self.assertEqual(
                [command for command, _ in runner.commands],
                [
                    ["git", "-C", "duckdb", "fetch", "origin", "--tags", "--force"],
                    ["git", "-C", "duckdb", "tag", "--points-at", "HEAD"],
                    ["git", "-C", "duckdb", "rev-parse", "HEAD"],
                ],
            )
            self.assertEqual(runner.env["DUCKDB_VERSION"], sha)
            self.assertIn("DuckDB tags pointing at HEAD: (none)", output.getvalue())
            self.assertIn(
                f"No supported DuckDB version tag found; using commit: {sha}",
                output.getvalue(),
            )
            self.assertEqual(
                github_environment.read_text(encoding="utf-8"),
                f"DUCKDB_VERSION={sha}\n",
            )
            self.assertEqual(
                github_output.read_text(encoding="utf-8"),
                f"duckdb_version={sha}\n",
            )
            runner.prebuilt.assert_called_once_with()

    def test_checkout_selects_highest_version_tag_and_logs_all_tags(self):
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
            runner = RecordingRunner(env)
            runner.captured_output = "\n".join(
                [
                    "v2.0.0-rc2",
                    "not-a-version",
                    "v2.0.0",
                    "v2.0.0-alpha9",
                ]
            )
            runner.prebuilt = mock.Mock()

            output = io.StringIO()
            with redirect_stdout(output):
                runner.checkout()

            self.assertEqual(
                [command for command, _ in runner.commands],
                [
                    ["git", "-C", "duckdb", "fetch", "origin", "--tags", "--force"],
                    ["git", "-C", "duckdb", "tag", "--points-at", "HEAD"],
                ],
            )
            self.assertEqual(runner.env["DUCKDB_VERSION"], "v2.0.0")
            self.assertIn(
                "DuckDB tags pointing at HEAD: "
                "not-a-version, v2.0.0, v2.0.0-alpha9, v2.0.0-rc2",
                output.getvalue(),
            )
            self.assertIn(
                "Selected DuckDB version tag: v2.0.0",
                output.getvalue(),
            )
            self.assertEqual(
                github_environment.read_text(encoding="utf-8"),
                "DUCKDB_VERSION=v2.0.0\n",
            )
            self.assertEqual(
                github_output.read_text(encoding="utf-8"),
                "duckdb_version=v2.0.0\n",
            )
            runner.prebuilt.assert_called_once_with()

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

    def test_prebuilt_duckdb_artifact_names_cover_native_matrix(self):
        expected = {
            "linux_amd64": "duckdb-static-libs-linux-amd64.tar.gz",
            "linux_arm64": "duckdb-static-libs-linux-arm64.tar.gz",
            "linux_amd64_musl": "duckdb-static-libs-linux-amd64-musl.tar.gz",
            "linux_arm64_musl": "duckdb-static-libs-linux-arm64-musl.tar.gz",
            "osx_amd64": "duckdb-static-libs-osx-amd64.tar.gz",
            "osx_arm64": "duckdb-static-libs-osx-arm64.tar.gz",
            "windows_amd64": "duckdb-static-libs-windows-amd64.tar.gz",
            "windows_arm64": "duckdb-static-libs-windows-arm64.tar.gz",
            "windows_amd64_mingw": "duckdb-static-libs-windows-mingw.tar.gz",
        }
        for architecture, artifact_name in expected.items():
            with self.subTest(architecture=architecture):
                self.assertEqual(
                    prebuilt_duckdb_artifact_name(architecture), artifact_name
                )
        self.assertEqual(prebuilt_duckdb_artifact_name("wasm_eh"), "")

    def test_duckdb_cli_artifact_names_cover_native_matrix(self):
        for architecture, suffix in (
            ("linux_amd64", "linux-amd64"),
            ("linux_arm64_musl", "linux-arm64-musl"),
            ("osx_arm64", "osx-arm64"),
            ("windows_amd64", "windows-amd64"),
            ("windows_arm64", "windows-arm64"),
            ("windows_amd64_mingw", "windows-mingw"),
        ):
            with self.subTest(architecture=architecture):
                self.assertEqual(
                    duckdb_cli_artifact_name(architecture),
                    f"duckdb-cli-{suffix}.tar.gz",
                )
        self.assertEqual(duckdb_cli_artifact_name("wasm_eh"), "")

    def bundle_runner(self, workspace, platform="linux", architecture="linux_amd64"):
        env = self.environment(workspace, platform, architecture)
        env["CI_PREBUILT_DUCKDB_SOURCE"] = "workflow"
        helper = Path(workspace) / "duckdb/scripts/ci/run_extension_autoload_tests.py"
        helper.parent.mkdir(parents=True, exist_ok=True)
        helper.write_text('parser.add_argument("--test-root-directory")\n')
        runner = RecordingRunner(env)
        runner.captured_output = "a" * 40
        return runner

    def test_bundle_artifact_names_cover_native_matrix(self):
        for architecture in (
            "linux_amd64",
            "linux_arm64_musl",
            "osx_arm64",
            "windows_amd64",
            "windows_arm64",
            "windows_amd64_mingw",
        ):
            self.assertEqual(
                duckdb_test_artifact_name(architecture),
                duckdb_cli_artifact_name(architecture).replace(
                    "duckdb-cli-", "duckdb-test-"
                ),
            )
        self.assertEqual(duckdb_test_artifact_name("wasm_eh"), "")

    def test_bundle_extracts_matching_pair_for_each_platform_without_running_a_build(
        self,
    ):
        for platform, architecture in (
            ("linux", "linux_amd64"),
            ("linux", "linux_arm64_musl"),
            ("macos", "osx_arm64"),
            ("windows", "windows_amd64"),
            ("windows", "windows_arm64"),
            ("windows", "windows_amd64_mingw"),
        ):
            with (
                self.subTest(architecture=architecture),
                tempfile.TemporaryDirectory() as directory,
            ):
                workspace = Path(directory)
                runner = self.bundle_runner(workspace, platform, architecture)
                executable = "duckdb.exe" if platform == "windows" else "duckdb"
                self.create_prebuilt_archive(
                    workspace / ".ci/duckdb-test" / architecture,
                    duckdb_test_artifact_name(architecture),
                    [
                        (executable, b"shell"),
                        ("extension/test/test.duckdb_extension", b"runner"),
                    ],
                )
                self.assertTrue(runner.prepare_test_bundle())
                build = workspace / "build/release"
                self.assertEqual((build / executable).read_bytes(), b"shell")
                if os.name != "nt":
                    self.assertTrue((build / executable).stat().st_mode & 0o111)
                self.assertEqual(
                    (build / "extension/test/test.duckdb_extension").read_bytes(),
                    b"runner",
                )
                self.assertEqual(runner.commands, [])

    def test_bundle_requires_both_regular_files_and_rejects_extra_paths(self):
        for members in (
            [],
            [("duckdb", b"shell")],
            [("duckdb", b"1"), ("duckdb", b"2")],
            [("duckdb", b"shell"), ("../test.duckdb_extension", b"runner")],
            [
                ("duckdb", b"shell"),
                ("extension/test/test.duckdb_extension", b"runner"),
                ("unexpected", b"file"),
            ],
        ):
            with (
                self.subTest(members=members),
                tempfile.TemporaryDirectory() as directory,
            ):
                workspace = Path(directory)
                runner = self.bundle_runner(workspace)
                self.create_prebuilt_archive(
                    workspace / ".ci/duckdb-test/linux_amd64",
                    duckdb_test_artifact_name("linux_amd64"),
                    members,
                )
                with self.assertRaisesRegex(ValueError, "must contain only"):
                    runner.prepare_test_bundle()
                self.assertFalse((workspace / "build/release/duckdb").exists())

    def test_missing_workflow_bundle_allows_source_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = self.bundle_runner(Path(directory))
            self.assertFalse(runner.prepare_test_bundle())
            self.assertEqual(runner.commands, [])
            self.assertFalse((Path(directory) / "build").exists())

    def test_duplicate_workflow_bundles_fail_clearly(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            artifact = duckdb_test_artifact_name("linux_amd64")
            for name in ("one", "two"):
                archive = workspace / ".ci/duckdb-test/linux_amd64" / name / artifact
                archive.parent.mkdir(parents=True)
                archive.touch()
            with self.assertRaisesRegex(FileNotFoundError, "found 2"):
                self.bundle_runner(workspace).prepare_test_bundle()

    def test_legacy_checkout_does_not_request_workflow_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "github-env"
            env = self.environment(directory)
            env["GITHUB_ENV"] = str(output)
            runner = RecordingRunner(env)
            runner.test_artifact()
            self.assertEqual(output.read_text(), "CI_DUCKDB_TEST_ARTIFACT=\n")
            self.assertEqual(runner.commands, [])

    def test_modern_checkout_requests_matching_workflow_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = self.bundle_runner(Path(directory))
            runner.test_artifact()
            self.assertEqual(
                runner.env["CI_DUCKDB_TEST_ARTIFACT"],
                "duckdb-test-linux-amd64.tar.gz",
            )

    def test_staged_bundle_uses_requested_commit_and_version(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            archive = self.create_prebuilt_archive(
                workspace / "staged",
                duckdb_test_artifact_name("linux_amd64"),
                [
                    ("duckdb", b"shell"),
                    ("extension/test/test.duckdb_extension", b"runner"),
                ],
            )
            runner = self.bundle_runner(workspace)
            runner.env["CI_PREBUILT_DUCKDB_SOURCE"] = "staged"
            with mock.patch.object(
                runner,
                "_open_prebuilt_url",
                return_value=io.BytesIO(archive.read_bytes()),
            ) as open_url:
                self.assertTrue(runner.prepare_test_bundle())
            self.assertIn(
                "/aaaaaaaaaa/v1.2.3/duckdb/duckdb/github_release/duckdb-test-linux-amd64.tar.gz",
                open_url.call_args.args[0],
            )

    def test_unavailable_staged_bundle_allows_source_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = self.bundle_runner(Path(directory))
            runner.env["CI_PREBUILT_DUCKDB_SOURCE"] = "staged"
            with mock.patch.object(
                runner,
                "_open_prebuilt_url",
                side_effect=urllib.error.URLError("missing"),
            ):
                self.assertFalse(runner.prepare_test_bundle())
            self.assertFalse(list(Path(directory).rglob("*.part")))
            self.assertFalse((Path(directory) / "build/release/duckdb").exists())

    def test_missing_staged_version_pointer_allows_source_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = self.bundle_runner(Path(directory))
            runner.env.update(CI_PREBUILT_DUCKDB_SOURCE="staged", DUCKDB_VERSION="")
            with mock.patch.object(
                runner, "_open_prebuilt_url", side_effect=urllib.error.URLError("missing")
            ) as open_url:
                self.assertFalse(runner.prepare_test_bundle())
            self.assertTrue(
                open_url.call_args.args[0].endswith("/latest_alpha_version.txt")
            )

    def test_explicit_source_build_does_not_fetch_test_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = self.bundle_runner(Path(directory))
            runner.env["CI_PREBUILT_DUCKDB_SOURCE"] = "disabled"
            with mock.patch.object(runner, "_open_prebuilt_url") as open_url:
                self.assertFalse(runner.prepare_test_bundle())
            open_url.assert_not_called()
            self.assertEqual(runner.commands, [])

    def test_legacy_checkout_keeps_source_build(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = RecordingRunner(self.environment(directory))
            self.assertFalse(runner.prepare_test_bundle())
            self.assertEqual(runner.commands, [])

    def test_older_autoload_helper_skips_combined_tests(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = self.bundle_runner(Path(directory))
            helper = Path(directory) / "duckdb/scripts/ci/run_extension_autoload_tests.py"
            helper.write_text('parser.add_argument("--extension-configs")\n')
            self.assertFalse(runner.prepare_test_bundle())
            self.assertEqual(runner.commands, [])
            runner.env.update(
                CI_EXTENSION_ARTIFACT_DIR=str(Path(directory) / "downloaded"),
                CI_LINUX_NATIVE_CONTAINER="true",
            )
            (Path(directory) / "downloaded").mkdir()
            runner.test()
            self.assertEqual(runner.commands, [])

    def test_build_uses_extensions_only_when_pair_and_libraries_are_available(self):
        for pair_available, libraries_available in (
            (True, True),
            (True, False),
            (False, True),
            (False, False),
        ):
            with (
                self.subTest(pair=pair_available, libraries=libraries_available),
                tempfile.TemporaryDirectory() as directory,
            ):
                runner = RecordingRunner(self.environment(directory))
                runner.env.update(
                    CI_PREBUILT_DUCKDB_SOURCE="staged",
                    DUCKDB_PREBUILT_LIBRARY="core.a" if libraries_available else "",
                    DUCKDB_PREBUILT_EXTENSIONS="core_functions;test",
                )
                with (
                    mock.patch.object(runner, "prebuilt", return_value=libraries_available),
                    mock.patch.object(runner, "prepare_prebuilt_duckdb"),
                    mock.patch.object(
                        runner, "prepare_test_bundle", return_value=pair_available
                    ),
                    mock.patch.object(runner, "build_linux"),
                ):
                    runner.build()
                environment = runner.build_environment()
                if pair_available and libraries_available:
                    self.assertEqual(environment["BUILD_EXTENSIONS_ONLY"], "1")
                    self.assertEqual(
                        environment["DUCKDB_PREBUILT_EXTENSIONS"], "core_functions"
                    )
                else:
                    self.assertNotIn("BUILD_EXTENSIONS_ONLY", environment)
                    self.assertEqual(environment["DUCKDB_PREBUILT_LIBRARY"], "")
                    self.assertEqual(environment["DUCKDB_PREBUILT_EXTENSIONS"], "")

    def test_fetch_prebuilt_duckdb_skips_non_release_builds(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.environment(directory)
            env["CI_BUILD_TYPE"] = "debug"
            runner = RecordingRunner(env)

            with mock.patch.object(runner, "_open_prebuilt_url") as open_url:
                fetched = runner.prebuilt()

            self.assertFalse(fetched)
            open_url.assert_not_called()
            self.assertNotIn("CI_PREBUILT_DUCKDB_ARTIFACT", runner.env)

    def test_workflow_build_continues_with_missing_artifacts_without_staged_downloads(self):
        for platform, architecture, library in (
            ("linux", "linux_amd64", "libduckdb_static.a"),
            ("macos", "osx_arm64", "libduckdb_static.a"),
            ("windows", "windows_amd64", "duckdb_static.lib"),
        ):
            for bundle_available, libraries_available in (
                (True, True),
                (True, False),
                (False, True),
                (False, False),
            ):
                with (
                    self.subTest(
                        platform=platform,
                        bundle=bundle_available,
                        libraries=libraries_available,
                    ),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    workspace = Path(directory)
                    runner = self.bundle_runner(workspace, platform, architecture)
                    artifact_root = workspace / ".ci/prebuilt-duckdb" / architecture
                    artifact_name = prebuilt_duckdb_artifact_name(architecture)
                    runner.env.update(
                        CI_PREBUILT_DUCKDB_SOURCE="workflow",
                        CI_PREBUILT_DUCKDB_ARTIFACT=artifact_name,
                        CI_PREBUILT_DUCKDB_PATH=str(artifact_root),
                        CI_LINUX_NATIVE_CONTAINER="true",
                        DUCKDB_PREBUILT_LIBRARY="stale-core",
                        DUCKDB_PREBUILT_EXTENSIONS="stale-extension",
                    )
                    if libraries_available:
                        self.create_prebuilt_archive(
                            artifact_root, artifact_name, [(library, b"core")]
                        )
                    if bundle_available:
                        executable = "duckdb.exe" if platform == "windows" else "duckdb"
                        self.create_prebuilt_archive(
                            workspace / ".ci/duckdb-test" / architecture,
                            duckdb_test_artifact_name(architecture),
                            [
                                (executable, b"shell"),
                                ("extension/test/test.duckdb_extension", b"runner"),
                            ],
                        )
                    with mock.patch.object(runner, "_open_prebuilt_url") as open_url:
                        runner.build()
                    open_url.assert_not_called()
                    self.assertEqual(runner.env["CI_PREBUILT_DUCKDB_SOURCE"], "workflow")
                    self.assertTrue(runner.commands)
                    environment = runner.commands[-1][1]["extra_env"]
                    if bundle_available and libraries_available:
                        self.assertEqual(
                            Path(environment["DUCKDB_PREBUILT_LIBRARY"]).read_bytes(),
                            b"core",
                        )
                    else:
                        self.assertEqual(environment["DUCKDB_PREBUILT_LIBRARY"], "")
                    self.assertEqual(environment["DUCKDB_PREBUILT_EXTENSIONS"], "")

    def test_workflow_build_rejects_corrupt_or_duplicate_static_archives(self):
        for failure in ("corrupt", "duplicate", "missing-library"):
            with (
                self.subTest(failure=failure),
                tempfile.TemporaryDirectory() as directory,
            ):
                workspace = Path(directory)
                runner = RecordingRunner(self.environment(workspace))
                artifact_root = workspace / ".ci/prebuilt-duckdb/linux_amd64"
                artifact_name = prebuilt_duckdb_artifact_name("linux_amd64")
                runner.env.update(
                    CI_PREBUILT_DUCKDB_SOURCE="workflow",
                    CI_PREBUILT_DUCKDB_ARTIFACT=artifact_name,
                    CI_PREBUILT_DUCKDB_PATH=str(artifact_root),
                    CI_LINUX_NATIVE_CONTAINER="true",
                )
                if failure == "corrupt":
                    artifact_root.mkdir(parents=True)
                    (artifact_root / artifact_name).write_bytes(b"not an archive")
                    expected_error = tarfile.ReadError
                elif failure == "duplicate":
                    for name in ("one", "two"):
                        self.create_prebuilt_archive(
                            artifact_root / name,
                            artifact_name,
                            [("libduckdb_static.a", b"core")],
                        )
                    expected_error = FileNotFoundError
                else:
                    self.create_prebuilt_archive(artifact_root, artifact_name, [("README", b"invalid")])
                    expected_error = ValueError
                with (
                    mock.patch.object(runner, "_open_prebuilt_url") as open_url,
                    self.assertRaises(expected_error),
                ):
                    runner.build()
                open_url.assert_not_called()
                self.assertEqual(runner.commands, [])

    def test_fetch_prebuilt_duckdb_uses_explicit_version(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            github_env = workspace / "github-env"
            env = self.environment(workspace)
            env.update(
                {
                    "GITHUB_ENV": str(github_env),
                    "CI_PREBUILT_DUCKDB_BASE_URL": "https://staging.example",
                    "CI_PREBUILT_DUCKDB_ARTIFACT": "matrix-static-libs.tar.gz",
                }
            )
            runner = RecordingRunner(env)
            runner.captured_output = "b15358c371daf10dad799a43b6167f6ccdce84d8"

            with mock.patch.object(
                runner,
                "_open_prebuilt_url",
                return_value=io.BytesIO(b"archive contents"),
            ) as open_url:
                fetched = runner.prebuilt()

            self.assertTrue(fetched)
            artifact_name = "matrix-static-libs.tar.gz"
            artifact_root = workspace / ".ci" / "prebuilt-duckdb" / "linux_amd64"
            self.assertEqual(
                (artifact_root / artifact_name).read_bytes(), b"archive contents"
            )
            self.assertEqual(
                runner.env["CI_PREBUILT_DUCKDB_ARTIFACT"], artifact_name
            )
            self.assertEqual(
                runner.env["CI_PREBUILT_DUCKDB_PATH"], str(artifact_root.resolve())
            )
            self.assertEqual(
                github_env.read_text(encoding="utf-8").splitlines(),
                [
                    f"CI_PREBUILT_DUCKDB_ARTIFACT={artifact_name}",
                    f"CI_PREBUILT_DUCKDB_PATH={artifact_root.resolve()}",
                ],
            )
            self.assertEqual(
                [call.args[0] for call in open_url.call_args_list],
                [
                    "https://staging.example/b15358c371/v1.2.3/"
                    "duckdb/duckdb/github_release/"
                    "matrix-static-libs.tar.gz",
                ],
            )

    def test_fetch_prebuilt_duckdb_commit_uses_latest_alpha_version(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.environment(directory)
            env["DUCKDB_VERSION"] = (
                "b15358c371daf10dad799a43b6167f6ccdce84d8"
            )
            runner = RecordingRunner(env)
            runner.captured_output = env["DUCKDB_VERSION"]

            with mock.patch.object(
                runner,
                "_open_prebuilt_url",
                side_effect=[
                    io.BytesIO(b"v2.0.0-alpha46057\n"),
                    io.BytesIO(b"archive contents"),
                ],
            ) as open_url:
                fetched = runner.prebuilt()

            self.assertTrue(fetched)
            self.assertEqual(
                [call.args[0] for call in open_url.call_args_list],
                [
                    "https://duckdb-staging.duckdb.org/b15358c371/"
                    "latest_alpha_version.txt",
                    "https://duckdb-staging.duckdb.org/b15358c371/"
                    "v2.0.0-alpha46057/duckdb/duckdb/github_release/"
                    "duckdb-static-libs-linux-amd64.tar.gz",
                ],
            )

    def test_fetch_prebuilt_duckdb_uses_explicit_alpha_version(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.environment(directory)
            env["DUCKDB_VERSION"] = "v2.0.0-alpha46057"
            runner = RecordingRunner(env)
            runner.captured_output = (
                "b15358c371daf10dad799a43b6167f6ccdce84d8"
            )

            with mock.patch.object(
                runner,
                "_open_prebuilt_url",
                return_value=io.BytesIO(b"archive contents"),
            ) as open_url:
                fetched = runner.prebuilt()

            self.assertTrue(fetched)
            open_url.assert_called_once_with(
                "https://duckdb-staging.duckdb.org/b15358c371/"
                "v2.0.0-alpha46057/duckdb/duckdb/github_release/"
                "duckdb-static-libs-linux-amd64.tar.gz"
            )

    def test_fetch_prebuilt_duckdb_falls_back_when_pointer_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.environment(directory)
            env["DUCKDB_VERSION"] = (
                "b15358c371daf10dad799a43b6167f6ccdce84d8"
            )
            runner = RecordingRunner(env)
            runner.captured_output = env["DUCKDB_VERSION"]
            missing = urllib.error.HTTPError(
                "https://staging.example/pointer", 404, "Not Found", {}, None
            )

            with mock.patch.object(
                runner, "_open_prebuilt_url", side_effect=missing
            ):
                fetched = runner.prebuilt()
            missing.close()

            self.assertFalse(fetched)
            self.assertNotIn("CI_PREBUILT_DUCKDB_ARTIFACT", runner.env)
            self.assertFalse((Path(directory) / ".ci").exists())

    def test_fetch_prebuilt_duckdb_falls_back_when_archive_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = RecordingRunner(self.environment(directory))
            runner.captured_output = "b15358c371daf10dad799a43b6167f6ccdce84d8"
            missing = urllib.error.HTTPError(
                "https://staging.example/archive", 404, "Not Found", {}, None
            )

            with mock.patch.object(
                runner,
                "_open_prebuilt_url",
                side_effect=missing,
            ):
                fetched = runner.prebuilt()
            missing.close()

            self.assertFalse(fetched)
            self.assertNotIn("CI_PREBUILT_DUCKDB_ARTIFACT", runner.env)
            artifact_root = (
                Path(directory) / ".ci" / "prebuilt-duckdb" / "linux_amd64"
            )
            self.assertEqual(list(artifact_root.iterdir()), [])

    def test_fetch_prebuilt_duckdb_falls_back_on_network_error(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = RecordingRunner(self.environment(directory))
            runner.captured_output = "b15358c371daf10dad799a43b6167f6ccdce84d8"

            with mock.patch.object(
                runner,
                "_open_prebuilt_url",
                side_effect=urllib.error.URLError("offline"),
            ):
                fetched = runner.prebuilt()

            self.assertFalse(fetched)
            self.assertNotIn("CI_PREBUILT_DUCKDB_ARTIFACT", runner.env)

    def test_build_fetches_staged_prebuilt_duckdb(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.environment(directory)
            env["CI_PREBUILT_DUCKDB_SOURCE"] = "staged"
            runner = RecordingRunner(env)

            with (
                mock.patch.object(runner, "prebuilt", return_value=True) as prebuilt,
                mock.patch.object(
                    runner, "prepare_prebuilt_duckdb"
                ) as prepare_prebuilt,
                mock.patch.object(runner, "build_linux") as build_linux,
            ):
                runner.build()

            prebuilt.assert_called_once_with()
            prepare_prebuilt.assert_called_once_with()
            build_linux.assert_called_once_with()

    def test_build_falls_back_to_source_when_staged_archive_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.environment(directory)
            env["CI_PREBUILT_DUCKDB_SOURCE"] = "staged"
            runner = RecordingRunner(env)

            with (
                mock.patch.object(runner, "prebuilt", return_value=False) as prebuilt,
                mock.patch.object(
                    runner, "prepare_prebuilt_duckdb"
                ) as prepare_prebuilt,
                mock.patch.object(runner, "build_linux") as build_linux,
            ):
                runner.build()

            prebuilt.assert_called_once_with()
            prepare_prebuilt.assert_not_called()
            build_linux.assert_called_once_with()

    def test_fetch_prebuilt_duckdb_rejects_invalid_staged_version(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.environment(directory)
            env["DUCKDB_VERSION"] = (
                "b15358c371daf10dad799a43b6167f6ccdce84d8"
            )
            runner = RecordingRunner(env)
            runner.captured_output = env["DUCKDB_VERSION"]

            with mock.patch.object(
                runner,
                "_open_prebuilt_url",
                return_value=io.BytesIO(b"../../unexpected\n"),
            ):
                with self.assertRaisesRegex(ValueError, "invalid staged DuckDB version"):
                    runner.prebuilt()

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

    def test_bundle_reuse_excludes_shell_and_test_archives_from_prebuilt_linking(self):
        for platform, architecture, prefix, suffix, core in (
            ("linux", "linux_amd64", "lib", ".a", "libduckdb_static.a"),
            ("windows", "windows_amd64", "", ".lib", "duckdb_static.lib"),
        ):
            with self.subTest(
                platform=platform
            ), tempfile.TemporaryDirectory() as directory:
                workspace = Path(directory)
                artifact_root = workspace / ".ci/prebuilt-duckdb" / architecture
                self.create_prebuilt_archive(
                    artifact_root,
                    prebuilt_duckdb_artifact_name(architecture),
                    [
                        (core, b"core"),
                        (f"{prefix}duckdb_shell{suffix}", b"shell"),
                        (f"{prefix}test_extension{suffix}", b"runner"),
                        (f"{prefix}json_extension{suffix}", b"json"),
                    ],
                )
                runner = RecordingRunner(
                    self.environment(workspace, platform, architecture)
                )
                runner.env.update(
                    CI_USE_TEST_BUNDLE="true",
                    CI_BUILD_DUCKDB_SHELL="true",
                    CI_PREBUILT_DUCKDB_ARTIFACT=prebuilt_duckdb_artifact_name(architecture),
                    CI_PREBUILT_DUCKDB_PATH=str(artifact_root),
                )
                runner.prepare_prebuilt_duckdb()
                self.assertEqual(runner.env["DUCKDB_PREBUILT_EXTENSIONS"], "json")
                self.assertEqual(
                    {path.name for path in (artifact_root / "extracted").iterdir()},
                    {core, f"{prefix}json_extension{suffix}"},
                )

    def test_prebuilt_duckdb_extracts_capability_libraries(self):
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
                    ("libduckdb_httplib.a", b"httplib"),
                    ("libduckdb_loadable_extensions.a", b"loadable extensions"),
                    ("libparquet_extension.a", b"parquet"),
                ],
            )
            env = self.environment(workspace)
            env.update(
                {
                    "CI_PREBUILT_DUCKDB_ARTIFACT": artifact_name,
                    "CI_PREBUILT_DUCKDB_PATH": str(artifact_root),
                    "CI_BUILD_DUCKDB_SHELL": "true",
                }
            )
            runner = RecordingRunner(env)
            runner.prepare_prebuilt_duckdb()

            extracted = artifact_root / "extracted"
            self.assertEqual(
                (extracted / "libduckdb_httplib.a").read_bytes(), b"httplib"
            )
            self.assertEqual(
                (extracted / "libduckdb_loadable_extensions.a").read_bytes(),
                b"loadable extensions",
            )
            # capabilities are not extensions
            self.assertEqual(runner.env["DUCKDB_PREBUILT_EXTENSIONS"], "parquet")

    def test_prebuilt_duckdb_skips_extensions_built_from_source(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            artifact_name = "duckdb-static-libs-linux-amd64.tar.gz"
            artifact_root = workspace / ".ci" / "prebuilt-duckdb" / "linux_amd64"
            self.create_prebuilt_archive(
                artifact_root,
                artifact_name,
                [
                    ("libduckdb_static.a", b"library"),
                    ("libparquet_extension.a", b"parquet"),
                    ("libhttpfs_extension.a", b"httpfs"),
                ],
            )
            # the Makefile always includes the root config, even when the matrix lists another file
            (workspace / "extension_config.cmake").write_text(
                "# duckdb_extension_load(parquet SOURCE_DIR /elsewhere)\n"
                "DUCKDB_EXTENSION_LOAD(HTTPFS # built here (see README)\n"
                "\tSOURCE_DIR ${CMAKE_CURRENT_LIST_DIR}\n"
                "\tINCLUDE_DIR ${CMAKE_CURRENT_LIST_DIR}/src/include\n"
                ")\n"
                "duckdb_extension_statically_link(httpfs)\n",
                encoding="utf-8",
            )
            group_config = workspace / "config" / "group.cmake"
            group_config.parent.mkdir()
            group_config.write_text("duckdb_extension_load(parquet)\n", encoding="utf-8")
            env = self.environment(workspace)
            env.update(
                {
                    "CI_PREBUILT_DUCKDB_ARTIFACT": artifact_name,
                    "CI_PREBUILT_DUCKDB_PATH": str(artifact_root),
                    "CI_EXTENSION_CONFIG_PATHS": '["config/group.cmake"]',
                }
            )
            runner = RecordingRunner(env)
            runner.prepare_prebuilt_duckdb()

            self.assertEqual(runner.env["DUCKDB_PREBUILT_EXTENSIONS"], "parquet")

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

    def test_native_cuda_settings_reach_setup_and_build(self):
        for version in ("12", "13"):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as directory:
                env = self.environment(directory)
                env.update({
                    "CI_LINUX_NATIVE_CONTAINER": "true",
                    "CI_CUDA_VERSION": version,
                    "CI_CUDA_ARCHS": " 80-real;90-real ",
                })
                runner = RecordingRunner(env)
                runner.setup_linux()
                runner.build_linux()
                self.assertEqual(len(runner.commands), 2)
                for _, kwargs in runner.commands:
                    forwarded = kwargs["extra_env"]
                    self.assertEqual(forwarded["VCPKG_CUDA_VERSION"], version)
                    self.assertEqual(forwarded["CUDAARCHS"], "80-real;90-real")
                    self.assertEqual(forwarded["VCPKG_CUDA_ARCHITECTURES"], "80-real;90-real")

    def test_native_cuda_defaults_do_not_override_architectures(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.environment(directory)
            env.update({"CI_LINUX_NATIVE_CONTAINER": "true", "CI_CUDA_ARCHS": " "})
            for version in ("", "12"):
                env["CI_CUDA_VERSION"] = version
                forwarded = RecordingRunner(env).build_environment()
                self.assertNotIn("CUDAARCHS", forwarded)
                self.assertNotIn("VCPKG_CUDA_ARCHITECTURES", forwarded)
                self.assertEqual(forwarded.get("VCPKG_CUDA_VERSION", ""), version)

    def test_sirius_cuda_native_build_patches_merged_vcpkg_manifest(self):
        for extension in ("sirius_cuda12", "sirius_cuda13", "sirius_cuda99"):
            with self.subTest(extension=extension), tempfile.TemporaryDirectory() as directory:
                env = self.environment(directory)
                env.update(
                    {
                        "CI_EXTENSION_NAME": extension,
                        "CI_LINUX_NATIVE_CONTAINER": "true",
                        "USE_MERGED_VCPKG_MANIFEST": "1",
                    }
                )
                runner = RecordingRunner(env)
                runner.build_linux()

                self.assertEqual(runner.commands[0][0], ["make", "extension_configuration"])
                jq_command = runner.commands[1][0]
                self.assertEqual(jq_command[:2], ["sh", "-c"])
                self.assertIn('"name": "dlpack", "version": "0.8"', jq_command[2])
                self.assertIn('"name": "flatbuffers", "version": "24.3.25"', jq_command[2])
                self.assertIn('.name != "openssl"', jq_command[2])
                self.assertEqual(
                    runner.commands[2][0],
                    [
                        "mv",
                        "build/extension_configuration/vcpkg.json.tmp",
                        "build/extension_configuration/vcpkg.json",
                    ],
                )
                self.assertEqual(
                    runner.commands[3][0],
                    ["make", "release", "EXTENSION_CONFIG_STEP="],
                )

    def test_sirius_cuda_manifest_patch_requires_merged_manifest(self):
        for extension, merged in (
            ("sirius", "1"),
            ("sirius_cuda", "1"),
            ("sirius_cuda12_test", "1"),
            ("sirius_cuda12", ""),
        ):
            with self.subTest(extension=extension, merged=merged), tempfile.TemporaryDirectory() as directory:
                env = self.environment(directory)
                env.update(
                    {
                        "CI_EXTENSION_NAME": extension,
                        "CI_LINUX_NATIVE_CONTAINER": "true",
                        "USE_MERGED_VCPKG_MANIFEST": merged,
                    }
                )
                runner = RecordingRunner(env)
                runner.build_linux()

                self.assertEqual(len(runner.commands), 1)
                self.assertEqual(runner.commands[0][0], ["make", "release"])

    def test_sirius_cuda_docker_build_patches_manifest_inside_container(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.environment(directory)
            env.update(
                {
                    "CI_EXTENSION_NAME": "sirius_cuda12",
                    "USE_MERGED_VCPKG_MANIFEST": "1",
                    "CI_VCPKG_URL": "https://github.com/microsoft/vcpkg.git",
                    "CI_VCPKG_COMMIT": "abc123",
                    "CI_VCPKG_OVERLAY_PORTS": "extension-ci-tools/vcpkg_ports",
                    "CI_VCPKG_OVERLAY_TRIPLETS": "extension-ci-tools/toolchains",
                }
            )
            runner = RecordingRunner(env)
            runner.build_linux()

            docker = runner.docker_arguments()
            commands = [command for command, _ in runner.commands]
            configure_index = commands.index([*docker, "make", "configure_ci"])
            generate_index = commands.index([*docker, "make", "extension_configuration"])
            patch_index = next(
                index
                for index, command in enumerate(commands)
                if command[: len(docker) + 2] == [*docker, "sh", "-c"]
            )
            move_index = commands.index(
                [
                    *docker,
                    "mv",
                    "build/extension_configuration/vcpkg.json.tmp",
                    "build/extension_configuration/vcpkg.json",
                ]
            )
            build_index = commands.index(
                [*docker, "make", "release", "EXTENSION_CONFIG_STEP="]
            )
            self.assertLess(configure_index, generate_index)
            self.assertLess(generate_index, patch_index)
            self.assertLess(patch_index, move_index)
            self.assertLess(move_index, build_index)

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
            env["VCPKG_TARGET_TRIPLET"] = "x64-windows-static-release-clangcl"
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
                ("windows_amd64", "x64-windows-static-release", "cl", True, None),
                (
                    "windows_amd64",
                    "x64-windows-static-release-clangcl",
                    "clang-cl",
                    True,
                    None,
                ),
                (
                    "windows_arm64",
                    "arm64-windows-static-release-clangcl",
                    "clang-cl",
                    True,
                    "arm64-pc-windows-msvc",
                ),
                (
                    "windows_arm64",
                    "arm64-windows-static-release",
                    "cl",
                    True,
                    None,
                ),
                ("windows_amd64_mingw", "x64-mingw-static", "cl", True, None),
                ("windows_amd64", "x64-windows", "cl", False, None),
            )
            for architecture, triplet, toolchain, expected, compiler_target in cases:
                with self.subTest(
                    architecture=architecture, triplet=triplet, toolchain=toolchain
                ):
                    env = self.environment(directory, "windows", architecture)
                    env["VCPKG_TARGET_TRIPLET"] = triplet
                    env["CC"] = toolchain
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
                    if compiler_target:
                        self.assertIn(
                            f"-DCMAKE_C_COMPILER_TARGET={compiler_target}", extension_flags
                        )
                        self.assertIn(
                            f"-DCMAKE_CXX_COMPILER_TARGET={compiler_target}", extension_flags
                        )
                    else:
                        self.assertNotIn("-DCMAKE_C_COMPILER_TARGET=", extension_flags)
                        self.assertNotIn("-DCMAKE_CXX_COMPILER_TARGET=", extension_flags)

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

    def test_upload_contains_only_extension_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            build = workspace / "build/release"
            extension = build / "repository/v1/linux_amd64/test.duckdb_extension"
            extension.parent.mkdir(parents=True)
            extension.write_text("test extension", encoding="utf-8")
            (build / "duckdb").write_text("shell", encoding="utf-8")
            output = workspace / "github-output"
            env = self.environment(workspace)
            env.update(CI_UPLOAD_ALL_EXTENSIONS="true", GITHUB_OUTPUT=str(output))
            RecordingRunner(env).upload()
            artifact_path = Path(
                next(
                    line.removeprefix("artifact_path=")
                    for line in output.read_text().splitlines()
                    if line.startswith("artifact_path=")
                )
            )
            self.assertEqual(
                [
                    path.relative_to(artifact_path).as_posix()
                    for path in artifact_path.rglob("*")
                    if path.is_file()
                ],
                ["v1/linux_amd64/test.duckdb_extension"],
            )

    def test_merged_artifacts_run_once_with_native_bundle_and_preserve_other_extensions(
        self,
    ):
        for platform, architecture in (
            ("linux", "linux_amd64"),
            ("linux", "linux_arm64"),
            ("windows", "windows_amd64"),
        ):
            with (
                self.subTest(platform=platform),
                tempfile.TemporaryDirectory() as directory,
            ):
                workspace = Path(directory)
                runner = self.bundle_runner(workspace, platform, architecture)
                downloaded = workspace / "downloaded-repository"
                extensions = downloaded / "v1" / architecture
                extensions.mkdir(parents=True)
                for name in ("quack", "spatial"):
                    (extensions / f"{name}.duckdb_extension").write_text(name)
                config = workspace / "group.cmake"
                config.write_text("duckdb_extension_load(quack LOAD_TESTS)\n")
                runner.env.update(
                    CI_EXTENSION_ARTIFACT_DIR=str(downloaded),
                    CI_EXTENSION_CONFIG_PATHS='["group.cmake"]',
                    CI_LINUX_NATIVE_CONTAINER="true",
                )
                executable = "duckdb.exe" if platform == "windows" else "duckdb"
                self.create_prebuilt_archive(
                    workspace / ".ci/duckdb-test" / architecture,
                    duckdb_test_artifact_name(architecture),
                    [
                        (executable, b"shell"),
                        ("extension/test/test.duckdb_extension", b"native runner"),
                    ],
                )
                runner.test()
                build = workspace / "build/release"
                self.assertEqual(
                    (
                        build / "repository/v1" / architecture / "test.duckdb_extension"
                    ).read_bytes(),
                    b"native runner",
                )
                self.assertEqual(
                    (
                        build / "repository/v1" / architecture / "quack.duckdb_extension"
                    ).read_text(),
                    "quack",
                )
                commands = [command for command, kwargs in runner.commands]
                self.assertEqual(len(commands), 1)
                self.assertIn("run_extension_autoload_tests.py", commands[0][1])
                self.assertNotIn("--test-root", commands[0])
                self.assertEqual(
                    runner.commands[0][1]["extra_env"]["LOCAL_EXTENSION_REPO"],
                    str((build / "repository").resolve()),
                )
                self.assertIn(
                    "LOAD_TESTS", (workspace / "extension_config.cmake").read_text()
                )

    def test_legacy_combined_tests_skip_without_running_commands(self):
        for platform, architecture in (
            ("linux", "linux_amd64"),
            ("linux", "linux_arm64"),
            ("macos", "osx_arm64"),
            ("windows", "windows_amd64"),
        ):
            with (
                self.subTest(platform=platform),
                tempfile.TemporaryDirectory() as directory,
            ):
                workspace = Path(directory)
                env = self.environment(workspace, platform, architecture)
                env.update(
                    CI_EXTENSION_ARTIFACT_DIR=str(workspace / "repository"),
                    CI_OSX_BUILD_ARCH="arm64",
                )
                runner = RecordingRunner(env)
                output = io.StringIO()
                with redirect_stdout(output):
                    runner.test()
                self.assertEqual(runner.commands, [])
                self.assertFalse((workspace / "build").exists())
                self.assertIn("Combined extension tests skipped", output.getvalue())

    def test_legacy_standalone_tests_keep_running(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = RecordingRunner(self.environment(directory))
            runner.env["CI_LINUX_NATIVE_CONTAINER"] = "true"
            runner.test()
            self.assertEqual(
                [command for command, _ in runner.commands], [["make", "test_release"]]
            )

    def test_missing_workflow_bundle_builds_combined_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            runner = self.bundle_runner(workspace)
            repository = workspace / "downloaded"
            repository.mkdir()
            runner.env.update(
                CI_EXTENSION_ARTIFACT_DIR=str(repository),
                CI_LINUX_NATIVE_CONTAINER="true",
            )
            runner.test()
            commands = [command for command, _ in runner.commands]
            self.assertIn("test-runtime.cmake", str(commands[0]))
            self.assertIn("run_extension_autoload_tests.py", str(commands[-1]))
            self.assertEqual(runner.env["CI_USE_TEST_BUNDLE"], "false")

    def test_bundle_test_failure_propagates(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = self.bundle_runner(Path(directory))
            runner.env["CI_USE_TEST_BUNDLE"] = "true"
            with (
                mock.patch.object(
                    runner,
                    "_run_bundle_tests",
                    side_effect=subprocess.CalledProcessError(
                        1, ["python", "run_tests.py"]
                    ),
                ),
                self.assertRaises(subprocess.CalledProcessError),
            ):
                runner.test()

    def test_standalone_bundle_tests_use_own_test_root_in_docker(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = self.bundle_runner(Path(directory))
            runner.env.update(
                CI_USE_TEST_BUNDLE="true",
                CI_TEST_CONFIG='{"test_env_variables": {"DUCKDB_TEST_SETTING": "value"}}',
            )
            runner.create_docker_environment()
            runner.test()
            self.assertEqual(len(runner.commands), 1)
            command = runner.commands[0][0]
            image = command.index("duckdb/linux_amd64")
            self.assertIn(
                "LOCAL_EXTENSION_REPO=/duckdb_build_dir/build/release/repository",
                command[:image],
            )
            self.assertEqual(command[image + 1], "python3")
            self.assertEqual(command[-2:], ["--test-root", "/duckdb_build_dir/test"])
            self.assertIn("/duckdb_build_dir/extension_config.cmake", command)
            docker_environment = (runner.workspace / "docker_env.txt").read_text()
            self.assertIn("BUILD_EXTENSIONS_ONLY=1\n", docker_environment)
            self.assertIn("DUCKDB_TEST_SETTING=value\n", docker_environment)

    def test_nested_bundle_tests_use_the_nested_docker_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            workspace = repository / "nested/extension"
            workspace.mkdir(parents=True)
            env = self.environment(repository)
            env.update(
                CI_BUILD_DIRECTORY="nested/extension",
                CI_USE_TEST_BUNDLE="true",
            )
            runner = RecordingRunner(env)
            runner.create_docker_environment()
            runner.test()
            command = runner.commands[0][0]
            self.assertIn(
                "LOCAL_EXTENSION_REPO=/duckdb_build_dir/nested/extension/build/release/repository",
                command,
            )
            self.assertIn(
                "/duckdb_build_dir/nested/extension/duckdb/scripts/ci/run_extension_autoload_tests.py",
                command,
            )
            self.assertEqual(
                command[command.index("--test-root-directory") + 1],
                "/duckdb_build_dir/nested/extension",
            )
            self.assertEqual(
                command[-2:], ["--test-root", "/duckdb_build_dir/nested/extension/test"]
            )

    def test_nested_combined_runtime_build_uses_the_nested_config_path(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            workspace = repository / "nested/extension"
            helper = workspace / "duckdb/scripts/ci/run_extension_autoload_tests.py"
            helper.parent.mkdir(parents=True)
            helper.write_text('parser.add_argument("--test-root-directory")\n')
            downloaded = workspace / "downloaded"
            downloaded.mkdir()
            env = self.environment(repository)
            env.update(
                CI_BUILD_DIRECTORY="nested/extension",
                CI_EXTENSION_ARTIFACT_DIR=str(downloaded),
                CI_PREBUILT_DUCKDB_SOURCE="disabled",
                CI_VCPKG_URL="url",
                CI_VCPKG_COMMIT="pin",
            )
            runner = RecordingRunner(env)
            runner.test()
            build = next(
                command
                for command, _ in runner.commands
                if "EXT_CONFIG=" in str(command)
            )
            self.assertIn(
                "EXT_CONFIG=/duckdb_build_dir/nested/extension/.ci/test-runtime.cmake",
                build,
            )

    def test_combined_source_fallback_builds_runtime_and_uses_original_test_config(self):
        for platform, architecture, native in (
            ("linux", "linux_amd64", "true"),
            ("linux", "linux_amd64_musl", "false"),
            ("windows", "windows_amd64", "false"),
        ):
            with self.subTest(
                platform=platform, native=native
            ), tempfile.TemporaryDirectory() as directory:
                workspace = Path(directory)
                runner = self.bundle_runner(workspace, platform, architecture)
                runner.env.update(
                    CI_EXTENSION_CONFIG_PATHS='["group.cmake"]',
                    CI_EXTENSION_ARTIFACT_DIR=str(workspace / "downloaded"),
                    CI_PREBUILT_DUCKDB_SOURCE="disabled",
                    CI_LINUX_NATIVE_CONTAINER=native,
                    CI_VCPKG_URL="url",
                    CI_VCPKG_COMMIT="pin",
                )
                (workspace / "downloaded").mkdir()
                (workspace / "group.cmake").write_text(
                    "duckdb_extension_load(quack LOAD_TESTS)\n"
                )
                runner.test()
                commands = [command for command, _ in runner.commands]
                source_build = next(
                    command for command in commands if "EXT_CONFIG=" in str(command)
                )
                self.assertIn("test-runtime.cmake", str(source_build))
                self.assertIn("EXTRA_EXTENSION_CONFIGS=", str(source_build))
                if platform == "linux" and native == "false":
                    self.assertEqual(source_build[0], "docker")
                    self.assertIn(
                        "EXT_CONFIG=/duckdb_build_dir/.ci/test-runtime.cmake", source_build
                    )
                self.assertIn("run_extension_autoload_tests.py", str(commands[-1]))
                self.assertIn(
                    "LOAD_TESTS", (workspace / "extension_config.cmake").read_text()
                )
                self.assertEqual((workspace / ".ci/test-runtime.cmake").read_text(), "")
                self.assertEqual(runner.env["CI_USE_TEST_BUNDLE"], "false")

    def test_runtime_extension_is_not_uploaded_with_group_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            root = workspace / "build/release/repository/v1/linux_amd64"
            root.mkdir(parents=True)
            for name in ("test", "quack"):
                (root / f"{name}.duckdb_extension").touch()
            output = workspace / "outputs"
            runner = RecordingRunner(self.environment(workspace))
            runner.env.update(
                CI_USE_TEST_BUNDLE="true",
                CI_UPLOAD_ALL_EXTENSIONS="true",
                GITHUB_OUTPUT=str(output),
            )
            runner.upload()
            path = Path(
                next(
                    line.split("=", 1)[1]
                    for line in output.read_text().splitlines()
                    if line.startswith("artifact_path=")
                )
            )
            self.assertEqual(
                [file.name for file in path.rglob("*.duckdb_extension")],
                ["quack.duckdb_extension"],
            )

    def test_merged_artifacts_preserve_extension_test_registration(self):
        env = self.environment(".")
        env.update(
            CI_EXTENSION_ARTIFACT_DIR="repository",
            DUCKDB_PREBUILT_EXTENSIONS="core_functions;httpfs;icu;json;parquet;tpch",
        )
        environment = RecordingRunner(env).build_environment()
        self.assertEqual(environment["DUCKDB_PREBUILT_EXTENSIONS"], "")
        self.assertEqual(environment["DUCKDB_PREBUILT_LIBRARY"], "")

    def test_windows_merged_artifact_build_does_not_require_ccache(self):
        env = self.environment(".", "windows", "windows_amd64")
        env["CI_EXTENSION_ARTIFACT_DIR"] = "repository"
        runner = RecordingRunner(env)
        runner.build_windows()
        self.assertNotIn("ccache", runner.commands[-1][1]["extra_env"]["EXT_FLAGS"])
        self.assertIn("vcvars64.bat", runner.commands[-1][0])

    def test_prebuilt_artifact_name_is_exported_for_download(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "github-env"
            env = self.environment(directory, "windows", "windows_amd64")
            env["GITHUB_ENV"] = str(output)
            runner = RecordingRunner(env)
            runner.prebuilt_artifact()
            self.assertEqual(
                output.read_text(),
                "CI_PREBUILT_DUCKDB_ARTIFACT=duckdb-static-libs-windows-amd64.tar.gz\n",
            )


if __name__ == "__main__":
    unittest.main()
