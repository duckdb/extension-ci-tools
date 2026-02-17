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

	data, err := os.ReadFile(filepath.Join("..", "..", "config", "distribution_matrix.json"))
	require.NoError(t, err)

	matrix, err := ParseMatrixFile(data)
	require.NoError(t, err)
	assert.Contains(t, matrix, "linux")
	assert.Contains(t, matrix, "osx")
	assert.Contains(t, matrix, "windows")
	assert.Contains(t, matrix, "wasm")
}
