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
