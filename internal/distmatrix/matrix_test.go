package distmatrix

import (
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestRenderGitHubOutputLines(t *testing.T) {
	matrices := map[string]PlatformMatrix{
		"linux": {
			Include: []PlatformOutput{{DuckDBArch: "linux_amd64"}},
		},
		"windows": {
			Include: []PlatformOutput{},
		},
	}

	content, err := RenderGitHubOutputLines(matrices)
	require.NoError(t, err)
	assert.Contains(t, content, "linux_matrix={\"include\":[{\"duckdb_arch\":\"linux_amd64\"}]}")
	assert.Contains(t, content, "windows_matrix={}")
}
