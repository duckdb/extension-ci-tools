import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
MAKE = shutil.which("make")


@unittest.skipUnless(MAKE and os.name == "posix", "These tests require Make and a POSIX shell")
class DuckDBExtensionMakefileTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name).resolve()
        self.environment = {key: os.environ[key] for key in ("PATH", "TMPDIR", "TMP", "TEMP") if key in os.environ}
        self.write_makefile()

    def write_makefile(self, settings=""):
        (self.project / "Makefile").write_text(
            "PROJ_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))\n"
            "EXT_NAME := iceberg\n"
            "EXT_CONFIG := $(PROJ_DIR)extension_config.cmake\n"
            "CORE_EXTENSIONS := 'httpfs;parquet;tpch'\n"
            f"{settings}\n"
            f"include {REPO_ROOT}/makefiles/duckdb_extension.Makefile\n"
        )

    def make(self, target="reldebug", variables=(), dry_run=True, environment=None):
        command = [MAKE]
        if dry_run:
            command.append("-n")
        command.extend([target, *variables])
        result = subprocess.run(
            command,
            cwd=self.project,
            env={**self.environment, **(environment or {})},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        return result.stdout

    def commands(self, **kwargs):
        return [shlex.split(line) for line in self.make(**kwargs).splitlines() if line.strip()]

    def sync_command(self, commands):
        return next(
            command for command in commands if any(arg.endswith("/sync_out_of_tree_extensions.py") for arg in command)
        )

    def configure_command(self, commands):
        return next(command for command in commands if command[0] in ("cmake", "emcmake") and "-S" in command)

    def assert_shared_selection(self, commands, expected):
        sync = self.sync_command(commands)
        configure = self.configure_command(commands)
        self.assertEqual(sync[sync.index("--build-extensions") + 1].split(";"), expected)
        self.assertIn(f"-DBUILD_EXTENSIONS={';'.join(expected)}", configure)

    def test_unexported_legacy_core_extensions_reach_both_steps_without_quotes(self):
        commands = self.commands(variables=["DUCKDB_NEW_EXTENSION_BUILD=1"])
        self.assert_shared_selection(commands, ["httpfs", "parquet", "tpch"])

    def test_modern_and_legacy_selections_are_combined(self):
        commands = self.commands(variables=["DUCKDB_NEW_EXTENSION_BUILD=1", "BUILD_EXTENSIONS=avro;aws"])
        self.assert_shared_selection(commands, ["avro", "aws", "httpfs", "parquet", "tpch"])

    def test_duckdb_extensions_alias_has_precedence_over_build_extensions(self):
        commands = self.commands(
            variables=["DUCKDB_NEW_EXTENSION_BUILD=1", "BUILD_EXTENSIONS=unused", "DUCKDB_EXTENSIONS=avro"]
        )
        self.assert_shared_selection(commands, ["avro", "httpfs", "parquet", "tpch"])

    def test_test_dependencies_are_added_even_with_command_line_core_extensions(self):
        self.write_makefile("DEFAULT_TEST_EXTENSION_DEPS := json\nFULL_TEST_EXTENSION_DEPS := icu;aws")
        for mode, expected in (
            ("default", ["httpfs", "json"]),
            ("full", ["httpfs", "json", "icu", "aws"]),
            ("none", ["httpfs"]),
        ):
            with self.subTest(mode=mode):
                commands = self.commands(
                    variables=[
                        "DUCKDB_NEW_EXTENSION_BUILD=1",
                        "CORE_EXTENSIONS=httpfs",
                        f"BUILD_EXTENSION_TEST_DEPS={mode}",
                    ]
                )
                self.assert_shared_selection(commands, expected)

    def test_custom_config_directory_and_extra_config_order(self):
        self.write_makefile(
            "EXTENSION_CONFIG_BASE_DIR := /project/custom configs\n"
            "EXTRA_EXTENSION_CONFIGS := /project/override one.cmake;/project/override two.cmake"
        )
        commands = self.commands(variables=["DUCKDB_NEW_EXTENSION_BUILD=1"])
        sync = self.sync_command(commands)
        configure = self.configure_command(commands)
        configs = f"/project/override one.cmake;/project/override two.cmake;{self.project}/extension_config.cmake"
        self.assertEqual(sync[sync.index("--extension-configs") + 1], configs)
        self.assertIn(f"-DDUCKDB_EXTENSION_CONFIGS={configs}", configure)
        self.assertEqual(sync[sync.index("--extension-config-base-dir") + 1], "/project/custom configs")
        self.assertIn("-DEXTENSION_CONFIG_BASE_DIR=/project/custom configs", configure)

    def test_complete_config_list_can_be_overridden_from_environment_or_command_line(self):
        configs = f"/project/override.cmake;{self.project}/extension_config.cmake"
        for from_environment in (False, True):
            with self.subTest(from_environment=from_environment):
                commands = self.commands(
                    variables=["DUCKDB_NEW_EXTENSION_BUILD=1"]
                    + ([] if from_environment else [f"EXTENSION_CONFIGS={configs}"]),
                    environment={"EXTENSION_CONFIGS": configs} if from_environment else {},
                )
                sync = self.sync_command(commands)
                self.assertEqual(sync[sync.index("--extension-configs") + 1], configs)
                self.assertIn(f"-DDUCKDB_EXTENSION_CONFIGS={configs}", self.configure_command(commands))

    def test_all_build_targets_sync_before_configuring_in_new_mode(self):
        for target in ("debug", "release", "reldebug", "relassert", "clangd", "wasm_mvp", "wasm_eh", "wasm_threads"):
            with self.subTest(target=target):
                commands = self.commands(
                    target=target, variables=["DUCKDB_NEW_EXTENSION_BUILD=1", "USE_MERGED_VCPKG_MANIFEST=1"]
                )
                sync = self.sync_command(commands)
                configure = self.configure_command(commands)
                self.assertLess(commands.index(sync), commands.index(configure))
                self.assertIn(f"-DVCPKG_MANIFEST_DIR={self.project}/build", configure)
                self.assertFalse(any("-DEXTENSION_CONFIG_BUILD=TRUE" in command for command in commands))

    def test_empty_selection_and_default_directory_do_not_add_sync_overrides(self):
        commands = self.commands(variables=["DUCKDB_NEW_EXTENSION_BUILD=1", "CORE_EXTENSIONS="])
        sync = self.sync_command(commands)
        self.assertNotIn("--build-extensions", sync)
        self.assertNotIn("--extension-config-base-dir", sync)
        self.assertFalse(any(arg.startswith("-DBUILD_EXTENSIONS=") for arg in self.configure_command(commands)))

    def test_legacy_merged_manifest_workflow_is_preserved(self):
        commands = self.commands(variables=["USE_MERGED_VCPKG_MANIFEST=1"])
        self.assertTrue(any("-DEXTENSION_CONFIG_BUILD=TRUE" in command for command in commands))
        self.assertFalse(any("sync_out_of_tree_extensions.py" in arg for command in commands for arg in command))
        self.assertIn("-DBUILD_EXTENSIONS=httpfs;parquet;tpch", self.configure_command(commands))
        self.assertTrue(
            any(f"-DVCPKG_MANIFEST_DIR={self.project}/build/extension_configuration" in command for command in commands)
        )

    def test_local_mode_setting_is_exported_and_sync_runs_before_cmake(self):
        self.write_makefile("DUCKDB_NEW_EXTENSION_BUILD := 1\nEXTENSION_CONFIG_BASE_DIR := /project/configs")
        scripts = self.project / "duckdb" / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "sync_out_of_tree_extensions.py").write_text(
            "import json, os, pathlib, sys\n"
            "with open(os.environ['COMMAND_LOG'], 'a') as log:\n"
            "    log.write(json.dumps(['sync', sys.argv[1:], os.environ.get('DUCKDB_NEW_EXTENSION_BUILD')]) + '\\n')\n"
            "pathlib.Path('build/vcpkg.json').write_text('{\"dependencies\": []}')\n"
        )
        binaries = self.project / "bin"
        binaries.mkdir()
        cmake = binaries / "cmake"
        cmake.write_text(
            f"#!{sys.executable}\n"
            "import json, os, pathlib, sys\n"
            "assert pathlib.Path('build/vcpkg.json').exists()\n"
            "with open(os.environ['COMMAND_LOG'], 'a') as log:\n"
            "    log.write(json.dumps(['cmake', sys.argv[1:], os.environ.get('DUCKDB_NEW_EXTENSION_BUILD')]) + '\\n')\n"
        )
        cmake.chmod(0o755)
        command_log = self.project / "commands.jsonl"
        self.make(
            dry_run=False,
            variables=[f"PYTHON={sys.executable}"],
            environment={
                "COMMAND_LOG": str(command_log),
                "PATH": str(binaries) + os.pathsep + self.environment.get("PATH", os.defpath),
            },
        )
        events = [json.loads(line) for line in command_log.read_text().splitlines()]
        self.assertEqual([event[0] for event in events], ["sync", "cmake", "cmake"])
        self.assertEqual([event[2] for event in events], ["1", "1", "1"])
        self.assertEqual(events[0][1][events[0][1].index("--build-extensions") + 1], "httpfs;parquet;tpch")
        self.assertIn("-DBUILD_EXTENSIONS=httpfs;parquet;tpch", events[1][1])
        self.assertIn("-DEXTENSION_CONFIG_BASE_DIR=/project/configs", events[1][1])


if __name__ == "__main__":
    unittest.main()
