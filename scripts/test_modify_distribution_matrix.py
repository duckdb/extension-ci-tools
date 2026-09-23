import json
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("modify_distribution_matrix.py")


class ModifyDistributionMatrixTest(unittest.TestCase):
    def test_runner_overrides_accept_names_and_label_arrays(self):
        matrix = {
            "linux": {
                "include": [
                    self.entry("linux_amd64"),
                    self.entry("linux_amd64_musl"),
                    self.entry("linux_arm64"),
                ]
            }
        }
        output = self.run_script(
            matrix,
            {
                "linux_x64": ["self-hosted", "linux", "x64"],
                "linux_amd64": "exact-runner",
            },
        )

        runners = {entry["duckdb_arch"]: entry["runner"] for entry in output["include"]}
        self.assertEqual(runners["linux_amd64"], "exact-runner")
        self.assertEqual(runners["linux_amd64_musl"], ["self-hosted", "linux", "x64"])
        self.assertEqual(runners["linux_arm64"], "default-runner")

    @staticmethod
    def entry(arch):
        return {
            "duckdb_arch": arch,
            "runner": "default-runner",
            "run_in_reduced_ci_mode": True,
            "opt_in": False,
        }

    def run_script(self, matrix, runners):
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "input.json"
            output_path = Path(directory) / "output.json"
            input_path.write_text(json.dumps(matrix))
            subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                    "--exclude",
                    "",
                    "--select_os",
                    "linux",
                    "--reduced_ci_mode",
                    "disabled",
                    "--runners",
                    json.dumps(runners),
                ],
                check=True,
            )
            return json.loads(output_path.read_text())


if __name__ == "__main__":
    unittest.main()
