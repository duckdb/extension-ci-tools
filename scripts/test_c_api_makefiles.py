import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


MAKEFILES_DIR = Path(__file__).resolve().parent.parent / "makefiles" / "c_api_extensions"


@unittest.skipIf(shutil.which("make") is None, "make is not installed")
class CCppWasmFlagsTest(unittest.TestCase):
    def dry_run(self, platform):
        with tempfile.TemporaryDirectory() as tmp:
            makefile = Path(tmp) / "Makefile"
            makefile.write_text(
                "EXTENSION_NAME=quack\n"
                "TARGET_DUCKDB_VERSION=v1.2.0\n"
                f"include {MAKEFILES_DIR / 'base.Makefile'}\n"
                f"include {MAKEFILES_DIR / 'c_cpp.Makefile'}\n"
                "release: build_extension_library_release build_extension_with_metadata_release\n"
            )
            env = {key: value for key, value in os.environ.items() if key not in ("CMAKE_C_FLAGS", "CMAKE_CXX_FLAGS")}
            result = subprocess.run(
                ["make", "-n", "release", f"DUCKDB_PLATFORM={platform}"],
                cwd=tmp,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
        lines = result.stdout.splitlines()
        configure = [line for line in lines if line.startswith("emcmake cmake ")]
        link = [line for line in lines if line.startswith("emcc ")]
        self.assertEqual(len(configure), 1, result.stdout)
        self.assertEqual(len(link), 1, result.stdout)
        return configure[0], link[0]

    def test_wasm_mvp_adds_no_flags(self):
        configure, link = self.dry_run("wasm_mvp")
        self.assertNotIn("CMAKE_C_FLAGS", configure)
        self.assertNotIn("CMAKE_CXX_FLAGS", configure)
        self.assertNotIn("-pthread", link)

    def test_wasm_eh_compiles_with_wasm_exceptions(self):
        configure, link = self.dry_run("wasm_eh")
        self.assertIn('-DCMAKE_CXX_FLAGS="-fwasm-exceptions"', configure)
        self.assertNotIn("-pthread", link)

    def test_wasm_threads_compiles_and_links_with_shared_memory(self):
        configure, link = self.dry_run("wasm_threads")
        self.assertIn('-DCMAKE_C_FLAGS="-pthread"', configure)
        self.assertIn(
            '-DCMAKE_CXX_FLAGS="-fwasm-exceptions -DWITH_WASM_THREADS=1 -DWITH_WASM_SIMD=1 '
            '-DWITH_WASM_BULK_MEMORY=1 -pthread"',
            configure,
        )
        self.assertIn("-pthread -sSHARED_MEMORY=1", link)


if __name__ == "__main__":
    unittest.main()
