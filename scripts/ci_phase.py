#!/usr/bin/env python3
"""Run command-based phases of the extension distribution workflow.

GitHub actions that need ``uses:`` remain in the workflow.  Everything else is
kept here so optional operations do not each become a top-level Actions step.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile
from typing import Mapping, Sequence


TRUE_VALUES = {"1", "true", "yes", "on"}


def format_command(command: Sequence[str] | str) -> str:
    return command if isinstance(command, str) else shlex.join(command)


def is_true(value: str | None) -> bool:
    return (value or "").lower() in TRUE_VALUES


def tool_enabled(toolchains: str, tool: str) -> bool:
    return f";{tool};" in f";{toolchains.strip(';')};"


def test_environment(config: str) -> dict[str, str]:
    parsed = json.loads(config or "{}")
    values = parsed.get("test_env_variables", {})
    if not isinstance(values, dict):
        raise ValueError("test_env_variables must be a JSON object")
    def stringify(value: object) -> str:
        if isinstance(value, bool):
            return str(value).lower()
        if value is None:
            return "null"
        return str(value)

    return {str(key): stringify(value) for key, value in values.items()}


def extra_dependencies(config: str, architecture: str) -> list[str]:
    if not config:
        return []
    parsed = json.loads(config)
    values = parsed.get(architecture, [])
    if not isinstance(values, list):
        raise ValueError(f"vcpkg dependencies for {architecture} must be a list")
    return [str(value) for value in values]


class PhaseRunner:
    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        self.env = dict(os.environ if environ is None else environ)
        self.platform = self.required("CI_PLATFORM")
        self.architecture = self.required("DUCKDB_PLATFORM")
        self.workspace = Path(self.env.get("GITHUB_WORKSPACE", ".")).resolve()

    def value(self, name: str, default: str = "") -> str:
        return self.env.get(name, default)

    def required(self, name: str) -> str:
        value = self.value(name)
        if not value:
            raise ValueError(f"missing required environment variable: {name}")
        return value

    def enabled(self, name: str) -> bool:
        return is_true(self.value(name))

    def run(
        self,
        command: Sequence[str] | str,
        *,
        extra_env: Mapping[str, str] | None = None,
        shell: bool = False,
        cwd: Path | None = None,
    ) -> None:
        environment = self.env.copy()
        if extra_env:
            environment.update(extra_env)
        print(f"+ {format_command(command)}", flush=True)
        options = {
            "check": True,
            "cwd": cwd or self.workspace,
            "env": environment,
            "shell": shell,
        }
        if shell and os.name != "nt":
            options["executable"] = shutil.which("bash") or "/bin/bash"
        subprocess.run(command, **options)

    def append_github_file(self, variable: str, name: str, value: str) -> None:
        destination = self.value(variable)
        if not destination:
            return
        with open(destination, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")

    def set_environment(self, name: str, value: str) -> None:
        self.env[name] = value
        self.append_github_file("GITHUB_ENV", name, value)

    def add_path(self, value: Path) -> None:
        self.env["PATH"] = f"{value}{os.pathsep}{self.env.get('PATH', '')}"
        destination = self.value("GITHUB_PATH")
        if destination:
            with open(destination, "a", encoding="utf-8") as handle:
                handle.write(f"{value}\n")

    def retry_prefix(self) -> list[str]:
        retry_script = self.workspace / "duckdb" / "scripts" / "ci" / "retry.py"
        return [sys.executable, str(retry_script), "--"] if retry_script.is_file() else []

    def run_with_retry(
        self, command: Sequence[str], *, extra_env: Mapping[str, str] | None = None
    ) -> None:
        self.run([*self.retry_prefix(), *command], extra_env=extra_env)

    def checkout(self) -> None:
        duckdb_repository = self.value("CI_DUCKDB_GIT_REPOSITORY")
        if duckdb_repository:
            self.run(
                ["make", "set_duckdb_repository"],
                extra_env={"DUCKDB_GIT_REPOSITORY": duckdb_repository},
            )

        duckdb_version = self.value("CI_DUCKDB_VERSION")
        if duckdb_version:
            self.run(
                ["make", "set_duckdb_version"],
                extra_env={"DUCKDB_GIT_VERSION": duckdb_version},
            )

        extension_tag = self.value("CI_EXTENSION_TAG")
        if extension_tag:
            self.run(["git", "tag", extension_tag])

        duckdb_tag = self.value("CI_DUCKDB_TAG")
        if duckdb_tag:
            self.run(["make", "set_duckdb_tag"], extra_env={"DUCKDB_TAG": duckdb_tag})

    def inject_extension_config(self) -> None:
        config_parts: list[str] = []
        raw_paths = self.value("CI_EXTENSION_CONFIG_PATHS", "[]")
        paths = json.loads(raw_paths)
        if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
            raise ValueError("CI_EXTENSION_CONFIG_PATHS must be a JSON string array")
        for configured_path in paths:
            path = Path(configured_path)
            if not path.is_absolute():
                path = self.workspace / path
            if not path.is_file():
                raise FileNotFoundError(f"extension config does not exist: {path}")
            config_parts.append(path.read_text(encoding="utf-8").rstrip("\n"))
        inline_config = self.value("CI_EXTRA_EXTENSION_CONFIG")
        if inline_config:
            config_parts.append(inline_config.rstrip("\n"))
        config = "\n\n".join(part for part in config_parts if part)
        if not config:
            return
        path = self.workspace / "extension_config.cmake"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n# Injected Extension Config\n{config}\n")
        print(path.read_text(encoding="utf-8"))

    def install_vcpkg(self) -> None:
        installation = self.workspace / "local_vcpkg_installation"
        installation.mkdir()
        self.run(["cmake", "--version"])
        self.run(["git", "init"], cwd=installation)
        self.run(
            ["git", "remote", "add", "origin", self.required("CI_VCPKG_URL")],
            cwd=installation,
        )
        commit = self.required("CI_VCPKG_COMMIT")
        self.run(["git", "fetch", "origin", commit], cwd=installation)
        self.run(["git", "checkout", commit], cwd=installation)
        self.run([str(installation / "bootstrap-vcpkg.sh")], cwd=installation)
        self.set_environment("VCPKG_ROOT", str(installation))
        self.set_environment(
            "VCPKG_TOOLCHAIN_PATH", str(installation / "scripts/buildsystems/vcpkg.cmake")
        )
        self.add_path(installation)

    def install_extra_vcpkg_dependencies(self, *, docker: bool = False) -> None:
        dependencies = extra_dependencies(
            self.value("CI_VCPKG_EXTRA_DEPENDENCIES"), self.architecture
        )
        for dependency in dependencies:
            if docker:
                self.run(
                    [
                        "docker",
                        "run",
                        "--env-file=docker_env.txt",
                        "-v",
                        f"{self.workspace}:/duckdb_build_dir",
                        f"duckdb/{self.architecture}",
                        "vcpkg",
                        "install",
                        dependency,
                        "--recurse",
                    ]
                )
            else:
                self.run(["vcpkg", "install", dependency, "--recurse"])

    def setup_linux(self) -> None:
        if self.enabled("CI_LINUX_NATIVE_CONTAINER"):
            self.run_with_retry(
                ["make", "configure_ci"],
                extra_env=self.build_environment(),
            )
            return
        if self.enabled("CI_USE_DEFAULT_RUNNERS"):
            if self.enabled("CI_RUN_DISK_CLEAN_STEP"):
                images = subprocess.run(
                    ["docker", "images", "-a", "-q"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.split()
                if images:
                    try:
                        self.run(["docker", "rmi", *images])
                    except subprocess.CalledProcessError as error:
                        print(f"Warning: could not remove preinstalled Docker images: {error}")
            self.run(["sudo", "systemctl", "stop", "docker"])
            self.run(["sudo", "mkdir", "-p", "/mnt/docker-data"])
            self.run(
                "echo '{ \"data-root\": \"/mnt/docker-data\" }' | sudo tee /etc/docker/daemon.json",
                shell=True,
            )
            self.run(["sudo", "systemctl", "start", "docker"])
            self.run("docker info | grep 'Docker Root Dir'", shell=True)

        self.run_with_retry(
            ["make", "configure_ci"],
            extra_env={
                "DUCKDB_GIT_VERSION": self.value("CI_DUCKDB_VERSION"),
                "LINUX_CI_IN_DOCKER": "0",
            },
        )

    def setup_macos(self) -> None:
        self.run(["brew", "install", "ninja", "autoconf", "make", "libtool", "automake", "autoconf-archive"])
        self.install_vcpkg()
        tools = self.value("CI_EXTRA_TOOLCHAINS")
        osx_arch = self.value("CI_OSX_BUILD_ARCH")
        if tool_enabled(tools, "rust") and osx_arch == "x86_64":
            self.run(["rustup", "target", "add", "x86_64-apple-darwin"])
        if tool_enabled(tools, "fortran"):
            self.run(["brew", "install", "gcc"])
        if tool_enabled(tools, "parser_tools"):
            self.run(["brew", "install", "bison", "flex"])
        if tool_enabled(tools, "omp"):
            self.setup_macos_omp()
        if tool_enabled(tools, "unixodbc"):
            self.setup_macos_unixodbc()
        if tool_enabled(tools, "downgraded_aws_cli"):
            self.run(["curl", "https://awscli.amazonaws.com/AWSCLIV2-2.22.35.pkg", "-o", "AWSCLIV2.pkg"])
            self.run(["sudo", "installer", "-pkg", "AWSCLIV2.pkg", "-target", "/"])
            self.run(["aws", "--version"])
        self.run(["make", "configure_ci"], extra_env={"DUCKDB_GIT_VERSION": self.value("CI_DUCKDB_VERSION")})
        self.install_extra_vcpkg_dependencies()

    def setup_macos_omp(self) -> None:
        if self.architecture == "osx_amd64":
            self.run(
                'arch -x86_64 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/master/install.sh)"',
                shell=True,
            )
            self.run(["arch", "-x86_64", "/usr/local/bin/brew", "install", "libomp"])
            prefix = "/usr/local/opt/libomp"
        else:
            self.run(["brew", "install", "libomp"])
            prefix = "/opt/homebrew/opt/libomp"
        self.set_environment("LDFLAGS", f"-L{prefix}/lib")
        self.set_environment("CFLAGS", f"-I{prefix}/include")
        self.set_environment("CPPFLAGS", f"-I{prefix}/include")
        self.set_environment("CXXFLAGS", f"-I{prefix}/include")

    def setup_macos_unixodbc(self) -> None:
        if self.architecture == "osx_amd64":
            self.run(
                'arch -x86_64 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"',
                shell=True,
            )
            brew = "/usr/local/bin/brew"
        else:
            brew = "brew"
        self.run([brew, "config"])
        self.run([brew, "install", "unixodbc"])
        self.run([brew, "ls", "-v", "unixodbc"])

    def setup_windows(self) -> None:
        tools = self.value("CI_EXTRA_TOOLCHAINS")
        if tool_enabled(tools, "downgraded_aws_cli"):
            command = (
                "$app = Get-WmiObject -Class Win32_Product -Filter \"Name LIKE 'AWS Command Line Interface%'\"; "
                "if ($app) { $app.Uninstall() }; "
                "Start-Process msiexec.exe -ArgumentList '/i https://awscli.amazonaws.com/AWSCLIV2-2.22.35.msi /qn' -Wait"
            )
            self.run(["powershell", "-NoProfile", "-Command", command])
        self.run(["aws", "--version"])
        if tool_enabled(tools, "parser_tools"):
            self.run(["choco", "install", "winflexbison3", "-y"])
        self.run(["choco", "install", "ninja", "-y"])
        self.run(["choco", "install", "jq", "-y"])

        if self.architecture in {"windows_amd64_rtools", "windows_amd64_mingw"}:
            root = Path("C:/rtools42/x86_64-w64-mingw32.static.posix/bin")
            for source, destination in (
                ("gcc.exe", "x86_64-w64-mingw32-gcc.exe"),
                ("g++.exe", "x86_64-w64-mingw32-g++.exe"),
                ("gfortran.exe", "x86_64-w64-mingw32-gfortran.exe"),
            ):
                shutil.copy2(root / source, root / destination)
            zstd = Path("C:/rtools42/usr/bin/zstd.exe")
            if zstd.exists():
                zstd.rename(zstd.with_name("zstd-rtools.exe"))

        self.run(["make", "configure_ci"], extra_env=self.build_environment())
        self.install_extra_vcpkg_dependencies()

    def setup_wasm(self) -> None:
        self.install_vcpkg()
        if tool_enabled(self.value("CI_EXTRA_TOOLCHAINS"), "downgraded_aws_cli"):
            self.run(["curl", "https://awscli.amazonaws.com/awscli-exe-linux-x86_64-2.22.35.zip", "-o", "awscliv2.zip"])
            self.run(["unzip", "-q", "awscliv2.zip"])
            self.run(["sudo", "./aws/install", "--update"])
            self.run(["aws", "--version"])
        self.run_with_retry(
            ["make", "configure_ci"],
            extra_env={"DUCKDB_GIT_VERSION": self.value("CI_DUCKDB_VERSION")},
        )
        self.install_extra_vcpkg_dependencies()

    def setup(self) -> None:
        self.inject_extension_config()
        getattr(self, f"setup_{self.platform}")()

    def build_environment(self) -> dict[str, str]:
        is_rtools = self.architecture in {"windows_amd64_rtools", "windows_amd64_mingw"}
        return {
            "DUCKDB_PLATFORM": self.architecture,
            "DUCKDB_PLATFORM_RTOOLS": "1" if is_rtools else "0",
            "DUCKDB_GIT_VERSION": self.value("CI_DUCKDB_VERSION"),
            "EXTENSION_NAME": self.required("CI_EXTENSION_NAME"),
            "EXTENSION_CANONICAL": self.value("CI_EXTENSION_CANONICAL"),
            "ENABLE_EXTENSION_AUTOINSTALL": "1",
            "ENABLE_EXTENSION_AUTOLOADING": "1",
        }

    def docker_arguments(self) -> list[str]:
        return [
            "docker",
            "run",
            "--env-file=docker_env.txt",
            "-v",
            f"{self.workspace}:/duckdb_build_dir",
            "-v",
            f"{self.workspace / '.ccache'}:/ccache_dir",
            f"duckdb/{self.architecture}",
        ]

    def create_docker_environment(self) -> None:
        values = {
            "VCPKG_BINARY_SOURCES": self.value("VCPKG_BINARY_SOURCES"),
            "USE_MERGED_VCPKG_MANIFEST": self.value("USE_MERGED_VCPKG_MANIFEST"),
            "AWS_ACCESS_KEY_ID": self.value("AWS_ACCESS_KEY_ID"),
            "AWS_SECRET_ACCESS_KEY": self.value("AWS_SECRET_ACCESS_KEY"),
            "AWS_ENDPOINT_URL": self.value("AWS_ENDPOINT_URL"),
            "AWS_DEFAULT_REGION": self.value("AWS_DEFAULT_REGION"),
            "AWS_REQUEST_CHECKSUM_CALCULATION": "when_required",
            "VCPKG_TARGET_TRIPLET": self.value("VCPKG_TARGET_TRIPLET"),
            "VCPKG_OVERLAY_TRIPLETS": f"/duckdb_build_dir/{self.value('CI_VCPKG_OVERLAY_TRIPLETS')}",
            "VCPKG_OVERLAY_PORTS": f"/duckdb_build_dir/{self.value('CI_VCPKG_OVERLAY_PORTS')}",
            "CUDAARCHS": self.value("CI_CUDA_ARCHS"),
            "VCPKG_CUDA_VERSION": self.value("CI_CUDA_VERSION"),
            "BUILD_SHELL": "1" if self.enabled("CI_BUILD_DUCKDB_SHELL") else "0",
            "OPENSSL_ROOT_DIR": f"/duckdb_build_dir/build/{self.value('CI_BUILD_TYPE')}/vcpkg_installed/{self.value('VCPKG_TARGET_TRIPLET')}",
            "OPENSSL_DIR": f"/duckdb_build_dir/build/{self.value('CI_BUILD_TYPE')}/vcpkg_installed/{self.value('VCPKG_TARGET_TRIPLET')}",
            "OPENSSL_USE_STATIC_LIBS": "true",
            "DUCKDB_PLATFORM": self.architecture,
            "DUCKDB_GIT_VERSION": self.value("CI_DUCKDB_VERSION"),
            "ENABLE_EXTENSION_AUTOINSTALL": "1",
            "ENABLE_EXTENSION_AUTOLOADING": "1",
            "EXTENSION_NAME": self.required("CI_EXTENSION_NAME"),
            "EXTENSION_CANONICAL": self.value("CI_EXTENSION_CANONICAL"),
            "LINUX_CI_IN_DOCKER": "1",
            "GITHUB_ACTIONS": "true",
            "CI": "true",
            "CCACHE_MAXSIZE": "5G",
            "SUBSET_EXTENSIONS_TESTS": self.value("CI_EXTENSIONS_TEST_SELECTION"),
        }
        values.update(test_environment(self.value("CI_TEST_CONFIG", "{}")))
        with (self.workspace / "docker_env.txt").open("w", encoding="utf-8") as handle:
            for key, value in values.items():
                value = value.rstrip("\r\n")
                if "\n" in value or "\r" in value:
                    raise ValueError(f"Docker environment value {key} contains a newline")
                handle.write(f"{key}={value}\n")

    def build_linux(self) -> None:
        if self.enabled("CI_LINUX_NATIVE_CONTAINER"):
            self.run_with_retry(
                ["make", self.required("CI_BUILD_TYPE")],
                extra_env=self.build_environment(),
            )
            return
        self.run(
            [
                "docker",
                "build",
                "--build-arg",
                f"vcpkg_url={self.required('CI_VCPKG_URL')}",
                "--build-arg",
                f"vcpkg_commit={self.required('CI_VCPKG_COMMIT')}",
                "--build-arg",
                f"extra_toolchains=;{self.value('CI_EXTRA_TOOLCHAINS').strip(';')};",
                "--build-arg",
                f"cuda_version={self.value('CI_CUDA_VERSION')}",
                "-t",
                f"duckdb/{self.architecture}",
                str(self.workspace / "extension-ci-tools" / "docker" / self.architecture),
            ]
        )
        self.create_docker_environment()
        self.install_extra_vcpkg_dependencies(docker=True)
        docker = self.docker_arguments()
        self.run_with_retry([*docker, "make", "configure_ci"])
        self.run_with_retry([*docker, "make", self.required("CI_BUILD_TYPE")])
        post_build = self.value("CI_POST_BUILD_COMMAND")
        if post_build:
            self.run(post_build, shell=True, extra_env={"MATRIX_RUNNER": self.value("CI_MATRIX_RUNNER")})

    def build_macos(self) -> None:
        self.run(["make", self.required("CI_BUILD_TYPE")], extra_env=self.build_environment())

    def build_windows(self) -> None:
        environment = self.build_environment()
        environment["EXT_FLAGS"] = (
            "-DCMAKE_C_COMPILER_LAUNCHER=ccache "
            "-DCMAKE_CXX_COMPILER_LAUNCHER=ccache"
        )
        rtools = environment["DUCKDB_PLATFORM_RTOOLS"] == "1"
        commands = ["setlocal EnableDelayedExpansion"]
        if not rtools:
            target = {"windows_amd64": "vcvars64.bat", "windows_arm64": "vcvarsarm64.bat"}.get(self.architecture)
            if target:
                vs18_vcvars = (
                    "C:\\Program Files\\Microsoft Visual Studio\\18\\Enterprise"
                    f"\\VC\\Auxiliary\\Build\\{target}"
                )
                vs2022_vcvars = (
                    "C:\\Program Files\\Microsoft Visual Studio\\2022\\Enterprise"
                    f"\\VC\\Auxiliary\\Build\\{target}"
                )
                vcvars = vs18_vcvars if os.path.isfile(vs18_vcvars) else vs2022_vcvars
                # vcvars changes this cmd.exe process's environment, so the build
                # must remain in the same shell invocation.
                commands.append(f'call "{vcvars}"')
            # Keep this optional rename separate from the &&-chained build command.
            # Otherwise cmd.exe treats the build as part of the IF body and skips it
            # when Git's link.exe is not present.
            self.run(
                'if exist "C:\\Program Files\\Git\\usr\\bin\\link.exe" '
                'move "C:\\Program Files\\Git\\usr\\bin\\link.exe" '
                '"C:\\Program Files\\Git\\usr\\bin\\link-git.exe"',
                shell=True,
                extra_env=environment,
            )
        commands.append(f"make {self.required('CI_BUILD_TYPE')}")
        # cmd.exe does not understand the C-runtime quote escaping used for argument lists.
        self.run(" && ".join(commands), shell=True, extra_env=environment)

    def build_wasm(self) -> None:
        self.run_with_retry(["make", self.architecture], extra_env=self.build_environment())

    def build(self) -> None:
        getattr(self, f"build_{self.platform}")()

    def test(self) -> None:
        if self.enabled("CI_SKIP_TESTS"):
            print("Tests skipped by workflow input.")
            return
        environment = self.build_environment()
        environment["SUBSET_EXTENSIONS_TESTS"] = self.value("CI_EXTENSIONS_TEST_SELECTION")
        environment.update(test_environment(self.value("CI_TEST_CONFIG", "{}")))
        target = f"test_{self.required('CI_BUILD_TYPE')}"

        if self.platform == "linux":
            if self.architecture == "linux_arm64":
                print("Tests are not supported for linux_arm64.")
                return
            if self.enabled("CI_LINUX_NATIVE_CONTAINER"):
                self.run(["make", target], extra_env=environment)
            else:
                self.run([*self.docker_arguments(), "make", target])
                environment["LINUX_CI_IN_DOCKER"] = "0"
                self.run(["make", target], extra_env=environment)
        elif self.platform == "macos":
            if self.value("CI_OSX_BUILD_ARCH") != "arm64":
                print("Tests run only on the native macOS arm64 build.")
                return
            self.run(["make", target], extra_env=environment)
        elif self.platform == "windows":
            self.run(["make", target], extra_env=environment)
        else:
            print("The Wasm distribution job has no test target.")

    def artifact_path(self) -> str:
        all_extensions = self.enabled("CI_UPLOAD_ALL_EXTENSIONS")
        extension = self.required("CI_EXTENSION_NAME")
        if self.platform == "wasm":
            base = f"build/{self.architecture}"
            return (
                f"{base}/repository/**/*.duckdb_extension.wasm"
                if all_extensions
                else f"{base}/extension/{extension}/{extension}.duckdb_extension.wasm"
            )
        base = f"build/{self.required('CI_BUILD_TYPE')}"
        return (
            f"{base}/repository/**/*.duckdb_extension"
            if all_extensions
            else f"{base}/extension/{extension}/{extension}.duckdb_extension"
        )

    def print_rust_logs(self) -> None:
        if not self.enabled("CI_RUST_LOGS") or not self.enabled("CI_HAS_RUST"):
            return
        pattern = self.workspace / "build" / self.required("CI_BUILD_TYPE") / "rust" / "src" / "**" / "*build-*.log"
        logs = sorted(glob.glob(str(pattern), recursive=True))
        if not logs:
            print(f"No Rust build logs found under {pattern.parent}")
            return
        for filename in logs:
            print(f"Printing logs for file {filename}")
            print(Path(filename).read_text(encoding="utf-8", errors="replace"))
            print(f"Done printing logs for file {filename}")

    def upload(self) -> None:
        path = self.artifact_path()
        matches = glob.glob(str(self.workspace / path), recursive=True)
        if not matches:
            raise FileNotFoundError(f"no artifact matched {path}")
        name = self.value("CI_ARTIFACT_NAME") or (
            f"{self.required('CI_EXTENSION_NAME')}-{self.value('CI_DUCKDB_VERSION')}-extension-"
            f"{self.architecture}{self.value('CI_ARTIFACT_POSTFIX')}"
        )
        self.append_github_file("GITHUB_OUTPUT", "artifact_path", path)
        self.append_github_file("GITHUB_OUTPUT", "artifact_name", name)
        self.print_rust_logs()

    def bundle_test_support(self) -> None:
        if self.enabled("CI_SKIP_TESTS") or self.platform == "wasm":
            return
        build_type = self.required("CI_BUILD_TYPE")
        build_dir = self.workspace / "build" / build_type
        if not build_dir.is_dir():
            raise FileNotFoundError(f"build directory does not exist: {build_dir}")

        artifact_root = self.workspace / ".ci" / "test-support" / build_type
        if artifact_root.parent.exists():
            shutil.rmtree(artifact_root.parent)
        artifact_root.mkdir(parents=True)

        required = [build_dir / "test" / "unittest"]
        if os.name == "nt":
            required = [build_dir / "test" / "unittest.exe"]
        for source in required:
            if not source.is_file():
                raise FileNotFoundError(f"test support file does not exist: {source}")

        candidates = [
            build_dir / "duckdb",
            build_dir / "duckdb.exe",
            build_dir / "test" / "run",
            build_dir / "test" / "run.exe",
            build_dir / "test" / "unittest",
            build_dir / "test" / "unittest.exe",
        ]
        for source in candidates:
            if source.is_file():
                destination = artifact_root / source.relative_to(build_dir)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)

        for pattern in (
            "src/libduckdb.*",
            "test/extension/*.duckdb_extension",
            "test/extension/*.duckdb_extension.wasm",
        ):
            for source in build_dir.glob(pattern):
                destination = artifact_root / source.relative_to(build_dir)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)

        archive = self.workspace / ".ci" / "test-support.tar.gz"
        with tarfile.open(archive, "w:gz", compresslevel=4) as bundle:
            bundle.add(artifact_root, arcname=build_type)
        self.append_github_file("GITHUB_OUTPUT", "test_support_path", str(archive))

    def test_supports(self) -> None:
        if self.enabled("CI_SKIP_TESTS"):
            print("Tests skipped by workflow input.")
            return
        support_root = Path(self.required("CI_TEST_SUPPORT_DIR"))
        repository_root = Path(self.required("CI_EXTENSION_ARTIFACT_DIR"))
        archives = sorted(support_root.glob("**/test-support.tar.gz"))
        if not archives:
            raise FileNotFoundError(f"no test-support archives found below {support_root}")

        build_type = self.required("CI_BUILD_TYPE")
        build_root = self.workspace / "build"
        build_dir = build_root / build_type
        for archive in archives:
            if build_dir.exists():
                shutil.rmtree(build_dir)
            with tarfile.open(archive, "r:gz") as bundle:
                members = bundle.getmembers()
                for member in members:
                    destination = (build_root / member.name).resolve()
                    if build_root.resolve() not in destination.parents and destination != build_root.resolve():
                        raise ValueError(f"unsafe path in test-support archive: {member.name}")
                bundle.extractall(build_root)
            destination_repository = build_dir / "repository"
            destination_repository.mkdir(parents=True, exist_ok=True)
            shutil.copytree(repository_root, destination_repository, dirs_exist_ok=True)
            print(f"Testing support bundle from {archive}")
            self.test()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "phase",
        choices=(
            "checkout",
            "setup",
            "build",
            "test",
            "upload",
            "bundle_test_support",
            "test_supports",
        ),
    )
    args = parser.parse_args()
    runner = PhaseRunner()
    try:
        getattr(runner, args.phase)()
    except subprocess.CalledProcessError as error:
        print(
            f"error: command failed with exit code {error.returncode}: "
            f"{format_command(error.cmd)}",
            file=sys.stderr,
        )
        raise SystemExit(error.returncode) from None


if __name__ == "__main__":
    main()
