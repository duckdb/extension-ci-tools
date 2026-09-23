"""Exercise workflow paths without downloading toolchains or building DuckDB."""

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github/workflows"


def load_workflow(name):
    return yaml.load((WORKFLOWS / name).read_text(), Loader=yaml.BaseLoader)


class ExtensionDirectoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.distribution = load_workflow("_extension_distribution.yml")
        cls.quality = load_workflow("_extension_code_quality.yml")

    def render(self, value, directory, workspace):
        replacements = {
            "inputs.extension_directory || '.'": directory or ".",
            "github.workspace": str(workspace),
            "inputs.build_type": "release",
            "inputs.extension_name": "quack",
            "matrix.duckdb_arch": "wasm_mvp",
            "matrix.vcpkg_target_triplet": "x64-linux-release",
        }
        return re.sub(r"\$\{\{\s*(.*?)\s*\}\}", lambda m: replacements[m[1]], value)

    def test_root_is_the_default(self):
        for workflow in (self.distribution, self.quality):
            self.assertEqual(
                workflow["on"]["workflow_call"]["inputs"]["extension_directory"][
                    "default"
                ],
                ".",
            )

    def test_native_builds_checks_and_dependency_paths(self):
        for directory in ("", ".", "extensions/quack"):
            workspace = Path("/checkout")
            extension = (workspace / (directory or ".")).resolve()
            for workflow in (self.distribution, self.quality):
                for name, job in workflow["jobs"].items():
                    if name == "generate_matrix":
                        continue
                    for step in job["steps"]:
                        with self.subTest(
                            directory=directory, job=name, step=step.get("name")
                        ):
                            script = step.get("run", "")
                            if (
                                re.search(
                                    r"\bmake (configure_ci|set_duckdb_|test_|format-check|tidy-check|\$\{\{)",
                                    script,
                                )
                                and "docker run" not in script
                            ) or step.get("name") in (
                                "Print Rust logs",
                                "Inject extra extension config",
                            ):
                                cwd = self.render(
                                    step.get("working-directory", "."),
                                    directory,
                                    workspace,
                                )
                                self.assertEqual((workspace / cwd).resolve(), extension)
                            if (
                                step.get("name") == "Checkout Extension CI tools"
                                or step.get("name")
                                == "Set DuckDB to ref of calling workflow"
                            ):
                                path = self.render(
                                    step["with"]["path"], directory, workspace
                                )
                                self.assertEqual(
                                    (workspace / path).resolve().parent, extension
                                )
                    for key in ("VCPKG_OVERLAY_PORTS", "VCPKG_OVERLAY_TRIPLETS"):
                        if key in job.get("env", {}):
                            path = Path(
                                self.render(job["env"][key], directory, workspace)
                            ).resolve()
                            self.assertEqual(
                                path.parent, extension / "extension-ci-tools"
                            )

    def test_artifact_uploads_find_nested_outputs(self):
        for directory in (".", "extensions/quack"):
            with tempfile.TemporaryDirectory() as tmp:
                workspace = Path(tmp)
                for job in self.distribution["jobs"].values():
                    for step in job["steps"]:
                        if not step.get("uses", "").startswith(
                            "actions/upload-artifact@"
                        ):
                            continue
                        pattern = self.render(
                            step["with"]["path"].strip(), directory, workspace
                        )
                        artifact = workspace / pattern.replace("**/", "quack/").replace(
                            "*.", "quack."
                        )
                        artifact.parent.mkdir(parents=True, exist_ok=True)
                        artifact.touch()
                        with self.subTest(directory=directory, pattern=pattern):
                            self.assertTrue(
                                artifact.is_relative_to(workspace / directory / "build")
                            )
                            self.assertIn(artifact, workspace.glob(pattern))

    def test_linux_container_preserves_parent_files_and_git(self):
        steps = self.distribution["jobs"]["linux"]["steps"]
        for directory in (".", "extensions/quack", "extensions/space quack"):
            with self.subTest(directory=directory), tempfile.TemporaryDirectory(
                prefix="workflow paths "
            ) as tmp:
                workspace = Path(tmp)
                extension = workspace / directory
                extension.mkdir(parents=True, exist_ok=True)
                subprocess.run(["git", "init", "-q", str(workspace)], check=True)
                (workspace / "shared.txt").write_text("shared engine source")
                (extension / "Makefile").write_text(
                    ".PHONY: configure_ci release test_release\n"
                    "configure_ci release test_release:\n"
                    '\t@test -f "$$(git rev-parse --show-toplevel)/shared.txt"\n'
                    "\t@test -d duckdb -a -d extension-ci-tools\n"
                )
                for dependency in ("duckdb", "extension-ci-tools"):
                    (extension / dependency).mkdir()
                bin_dir = workspace / "bin"
                bin_dir.mkdir()
                docker = bin_dir / "docker"
                docker.write_text(
                    "#!/usr/bin/env python3\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n"
                )
                docker.chmod(0o755)
                env = dict(
                    os.environ,
                    GITHUB_WORKSPACE=str(workspace),
                    EXTENSION_DIRECTORY=directory,
                )
                env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]
                for name in (
                    "Run configure (inside Docker)",
                    "Build extension (inside Docker)",
                    "Test extension (inside docker)",
                ):
                    step = next(step for step in steps if step.get("name") == name)
                    script = self.render(step["run"], directory, workspace)
                    result = subprocess.run(
                        ["bash", "-eu", "-c", script],
                        cwd=workspace,
                        env=env,
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    args = json.loads(result.stdout)
                    mounts = [args[i + 1] for i, arg in enumerate(args) if arg == "-v"]
                    self.assertIn(f"{workspace}:/duckdb_build_dir", mounts)
                    self.assertIn(f"{workspace}/ccache_dir:/ccache_dir", mounts)
                    container_cwd = Path(args[args.index("-w") + 1])
                    host_cwd = workspace / container_cwd.relative_to(
                        "/duckdb_build_dir"
                    )
                    self.assertEqual(host_cwd.resolve(), extension.resolve())
                    subprocess.run(
                        args[args.index("make") :], cwd=host_cwd, env=env, check=True
                    )

    def test_linux_dependency_paths_follow_container_directory(self):
        step = next(
            s
            for s in self.distribution["jobs"]["linux"]["steps"]
            if s.get("name") == "Create env file for docker"
        )
        for directory in (".", "extensions/quack"):
            for line in step["run"].splitlines():
                key, _, value = line.partition("=")
                if key not in (
                    "VCPKG_OVERLAY_TRIPLETS",
                    "VCPKG_OVERLAY_PORTS",
                    "OPENSSL_ROOT_DIR",
                    "OPENSSL_DIR",
                ):
                    continue
                path = self.render(value, directory, Path("/checkout")).replace(
                    "$EXTENSION_DIRECTORY", directory
                )
                expected = Path("/duckdb_build_dir") / directory
                self.assertTrue(Path(path).is_relative_to(expected))
                if key.startswith("VCPKG_OVERLAY"):
                    self.assertEqual(Path(path).parent, expected / "extension-ci-tools")

    def test_matrix_generation_stays_at_workspace_root(self):
        for step in self.distribution["jobs"]["generate_matrix"]["steps"]:
            self.assertNotIn("working-directory", step)
            if step.get("name") == "Checkout Extension CI tools":
                self.assertEqual(step["with"]["path"], "extension-ci-tools")


if __name__ == "__main__":
    unittest.main()
