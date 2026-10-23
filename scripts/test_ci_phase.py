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
                "version=v2.0.0-alpha48877\n",
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
                "version=v2.0.0\n",
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
