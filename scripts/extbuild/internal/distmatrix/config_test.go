package distmatrix

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestParseDistributionMatrixConfigFile(t *testing.T) {
	t.Parallel()

	data, err := os.ReadFile(filepath.Join("..", "..", "..", "..", "config", "distribution_matrix.json"))
	require.NoError(t, err)

	matrix, err := ParseMatrixFile(data)
	require.NoError(t, err)
	assert.Contains(t, matrix, "linux")
	assert.Contains(t, matrix, "osx")
	assert.Contains(t, matrix, "windows")
	assert.Contains(t, matrix, "wasm")

	platforms, err := ComputePlatformMatrices(matrix, ComputeOptions{
		Platform: "linux;osx;windows;wasm",
		Arch:     "amd64;arm64",
	})
	require.NoError(t, err)
	assert.Contains(t, platforms, "linux")
	assert.Contains(t, platforms, "osx")
	assert.Contains(t, platforms, "windows")
	assert.Contains(t, platforms, "wasm")
}

func TestWindowsVCPKGToolchainSelection(t *testing.T) {
	t.Parallel()

	data, err := os.ReadFile(filepath.Join("..", "..", "..", "..", "config", "distribution_matrix.json"))
	require.NoError(t, err)
	matrix, err := ParseMatrixFile(data)
	require.NoError(t, err)

	for _, tc := range []struct {
		toolchain string
		suffix    string
	}{
		{toolchain: "cl"},
		{toolchain: "clang-cl", suffix: "-clangcl"},
	} {
		t.Run(tc.toolchain, func(t *testing.T) {
			platforms, computeErr := ComputePlatformMatrices(matrix, ComputeOptions{
				Platform:              "windows",
				OptIn:                 "windows_arm64",
				ReducedCIMode:         ReducedCIDisabled,
				WindowsVCPKGToolchain: tc.toolchain,
			})
			require.NoError(t, computeErr)

			triplets := map[string]string{}
			for _, entry := range platforms["windows"].Include {
				triplets[entry.DuckDBArch] = entry.VCPKGTargetTriplet
				assert.Equal(t, entry.VCPKGTargetTriplet, entry.VCPKGHostTriplet)
			}
			assert.Equal(t, "x64-windows-static-release"+tc.suffix, triplets["windows_amd64"])
			assert.Equal(t, "arm64-windows-static-release"+tc.suffix, triplets["windows_arm64"])
			assert.Equal(t, "x64-mingw-static", triplets["windows_amd64_mingw"])
		})
	}
}

func TestParseWindowsVCPKGToolchainRejectsUnknownValue(t *testing.T) {
	t.Parallel()

	_, err := ParseWindowsVCPKGToolchain("clangcl")
	require.ErrorContains(t, err, "must be cl|clang-cl")
}

func TestParseMatrixFileRejectsUnknownFields(t *testing.T) {
	t.Parallel()

	const inputJSON = `{
  "linux": {
    "include": [
      {
        "duckdb_arch": "linux_amd64",
        "run_in_reduced_ci_mode": true,
        "opt_in": false,
        "unexpected": "value"
      }
    ]
  }
}`

	_, err := ParseMatrixFile([]byte(inputJSON))
	require.Error(t, err)
	require.ErrorContains(t, err, "unknown field")
}

func TestRunnerOverrideAliasesCoverAllRunnerEntriesInDistributionMatrix(t *testing.T) {
	t.Parallel()

	data, err := os.ReadFile(filepath.Join("..", "..", "..", "..", "config", "distribution_matrix.json"))
	require.NoError(t, err)

	matrix, err := ParseMatrixFile(data)
	require.NoError(t, err)

	expectedAliases := map[string]string{
		"linux_amd64":         "linux_x64",
		"linux_arm64":         "linux_arm64",
		"linux_amd64_musl":    "linux_x64",
		"linux_arm64_musl":    "linux_arm64",
		"osx_amd64":           "macos_x64",
		"osx_arm64":           "macos_arm64",
		"windows_amd64":       "windows_x64",
		"windows_arm64":       "windows_arm64",
		"windows_amd64_mingw": "windows_x64",
		"wasm_mvp":            "linux_x64",
		"wasm_eh":             "linux_x64",
		"wasm_threads":        "linux_x64",
	}

	actualAliases := map[string]string{}
	for _, cfg := range matrix {
		for _, entry := range cfg.Include {
			require.NotEmpty(t, entry.Runner, "runner must be set for %s", entry.DuckDBArch)

			actualAliases[entry.DuckDBArch] = runnerOverrideAliases(entry.DuckDBArch)
		}
	}

	assert.Equal(t, expectedAliases, actualAliases)
}
