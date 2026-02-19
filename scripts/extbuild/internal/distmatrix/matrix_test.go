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

	content, err := RenderGitHubOutputLines(matrices, MachineReadable)
	require.NoError(t, err)
	assert.Contains(t, content, "linux_matrix={\"include\":[{\"duckdb_arch\":\"linux_amd64\"}]}")
	assert.Contains(t, content, "windows_matrix={}")

	readable, err := RenderGitHubOutputLines(matrices, HumanReadable)
	require.NoError(t, err)
	assert.Contains(t, readable, "linux_matrix={\n")
	assert.Contains(t, readable, "windows_matrix={}")
}

func TestRenderDeployOutput(t *testing.T) {
	matrices := map[string]PlatformMatrix{
		"windows": {
			Include: []PlatformOutput{{DuckDBArch: "windows_amd64"}},
		},
		"linux": {
			Include: []PlatformOutput{
				{DuckDBArch: "linux_amd64"},
				{DuckDBArch: "linux_arm64"},
			},
		},
	}

	content, err := RenderDeployGitHubOutputLine(matrices)
	require.NoError(t, err)
	assert.Equal(t, "deploy_matrix={\"include\":[{\"duckdb_arch\":\"linux_amd64\"},{\"duckdb_arch\":\"linux_arm64\"},{\"duckdb_arch\":\"windows_amd64\"}]}\n", content)

	readable := RenderDeployReadableLines(matrices)
	assert.Equal(t, "linux_amd64\nlinux_arm64\nwindows_amd64\n", readable)
}

func TestParseReducedCIMode(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name    string
		input   string
		want    ReducedCIMode
		wantErr bool
	}{
		{
			name:  "empty maps to auto",
			input: "",
			want:  ReducedCIAuto,
		},
		{
			name:  "auto stays auto",
			input: string(ReducedCIAuto),
			want:  ReducedCIAuto,
		},
		{
			name:  "enabled stays enabled",
			input: string(ReducedCIEnabled),
			want:  ReducedCIEnabled,
		},
		{
			name:  "disabled stays disabled",
			input: string(ReducedCIDisabled),
			want:  ReducedCIDisabled,
		},
		{
			name:    "invalid value errors",
			input:   "sometimes",
			wantErr: true,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			got, err := ParseReducedCIMode(tc.input)
			if tc.wantErr {
				require.Error(t, err)
				return
			}

			require.NoError(t, err)
			assert.Equal(t, tc.want, got)
		})
	}
}
