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

func TestOSXAMD64RequiresExplicitOptInRegardlessOfReducedCIMode(t *testing.T) {
	t.Parallel()

	data, err := os.ReadFile(filepath.Join("..", "..", "..", "..", "config", "distribution_matrix.json"))
	require.NoError(t, err)

	matrix, err := ParseMatrixFile(data)
	require.NoError(t, err)

	disabled, err := ComputePlatformMatrices(matrix, ComputeOptions{
		Platform:      "osx",
		ReducedCIMode: ReducedCIDisabled,
	})
	require.NoError(t, err)
	assert.Equal(t, []string{"osx_arm64"}, platformArchs(disabled["osx"]))
	disabledDeploy, err := RenderDeployGitHubOutputLine(disabled)
	require.NoError(t, err)
	assert.NotContains(t, disabledDeploy, "osx_amd64")

	enabled, err := ComputePlatformMatrices(matrix, ComputeOptions{
		Platform:      "osx",
		ReducedCIMode: ReducedCIEnabled,
	})
	require.NoError(t, err)
	assert.Empty(t, enabled["osx"].Include)

	optedIn, err := ComputePlatformMatrices(matrix, ComputeOptions{
		Platform:      "osx",
		OptIn:         "osx_amd64",
		ReducedCIMode: ReducedCIEnabled,
	})
	require.NoError(t, err)
	assert.Equal(t, []string{"osx_amd64"}, platformArchs(optedIn["osx"]))
	optedInDeploy, err := RenderDeployGitHubOutputLine(optedIn)
	require.NoError(t, err)
	assert.Contains(t, optedInDeploy, "osx_amd64")

	excluded, err := ComputePlatformMatrices(matrix, ComputeOptions{
		Platform:      "osx",
		Exclude:       "osx_amd64",
		OptIn:         "osx_amd64",
		ReducedCIMode: ReducedCIDisabled,
	})
	require.NoError(t, err)
	assert.Equal(t, []string{"osx_arm64"}, platformArchs(excluded["osx"]))
}

func TestExcludeWasmEHAliasSkipsWasmBase(t *testing.T) {
	t.Parallel()

	data, err := os.ReadFile(filepath.Join("..", "..", "..", "..", "config", "distribution_matrix.json"))
	require.NoError(t, err)

	matrix, err := ParseMatrixFile(data)
	require.NoError(t, err)

	excluded, err := ComputePlatformMatrices(matrix, ComputeOptions{
		Platform:      "wasm",
		Exclude:       "wasm_eh",
		ReducedCIMode: ReducedCIDisabled,
	})
	require.NoError(t, err)
	assert.Equal(t, []string{"wasm_threads"}, platformArchs(excluded["wasm"]))

	notExcluded, err := ComputePlatformMatrices(matrix, ComputeOptions{
		Platform:      "wasm",
		Exclude:       "wasm_mvp",
		ReducedCIMode: ReducedCIDisabled,
	})
	require.NoError(t, err)
	assert.Equal(t, []string{"wasm_base", "wasm_threads"}, platformArchs(notExcluded["wasm"]))
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
		"wasm_base":           "linux_x64",
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

func platformArchs(matrix PlatformMatrix) []string {
	archs := make([]string, 0, len(matrix.Include))
	for _, entry := range matrix.Include {
		archs = append(archs, entry.DuckDBArch)
	}
	return archs
}
