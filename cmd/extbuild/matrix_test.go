package main

import (
	"testing"

	"github.com/duckdb/extension-ci-tools/internal/distmatrix"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestComputePlatformMatrices(t *testing.T) {
	t.Parallel()

	const inputJSON = `{
  "linux": {
    "include": [
      {
        "duckdb_arch": "linux_amd64",
        "runner": "ubuntu-24.04",
        "run_in_reduced_ci_mode": true,
        "opt_in": false
      },
      {
        "duckdb_arch": "linux_arm64",
        "runner": "ubuntu-24.04-arm",
        "run_in_reduced_ci_mode": false,
        "opt_in": false
      },
      {
        "duckdb_arch": "linux_amd64_musl",
        "runner": "ubuntu-24.04",
        "run_in_reduced_ci_mode": false,
        "opt_in": true
      }
    ]
  },
  "windows": {
    "include": [
      {
        "duckdb_arch": "windows_amd64",
        "runner": "windows-latest",
        "run_in_reduced_ci_mode": true,
        "opt_in": false
      },
      {
        "duckdb_arch": "windows_amd64_mingw",
        "runner": "windows-latest",
        "run_in_reduced_ci_mode": true,
        "opt_in": false
      },
      {
        "duckdb_arch": "windows_arm64",
        "runner": "windows-11-arm",
        "run_in_reduced_ci_mode": false,
        "opt_in": true
      }
    ]
  },
  "wasm": {
    "include": [
      {
        "duckdb_arch": "wasm_mvp",
        "run_in_reduced_ci_mode": true,
        "opt_in": false
      },
      {
        "duckdb_arch": "wasm_eh",
        "run_in_reduced_ci_mode": false,
        "opt_in": false
      }
    ]
  }
}`

	matrix, err := distmatrix.ParseMatrixFile([]byte(inputJSON))
	require.NoError(t, err)

	tests := []struct {
		name     string
		opts     distmatrix.ComputeOptions
		expected map[string][]string
	}{
		{
			name: "selected platforms only with arch token filters",
			opts: distmatrix.ComputeOptions{
				Platforms:     []string{"linux", "windows"},
				ArchTokens:    []string{"amd64"},
				ReducedCIMode: distmatrix.ReducedCIDisabled,
			},
			expected: map[string][]string{
				"linux":   {"linux_amd64"},
				"windows": {"windows_amd64", "windows_amd64_mingw"},
			},
		},
		{
			name: "arch omitted includes all for selected platform",
			opts: distmatrix.ComputeOptions{
				Platforms:     []string{"wasm"},
				ReducedCIMode: distmatrix.ReducedCIDisabled,
			},
			expected: map[string][]string{
				"wasm": {"wasm_eh", "wasm_mvp"},
			},
		},
		{
			name: "reduced ci mode enabled",
			opts: distmatrix.ComputeOptions{
				Platforms:     []string{"linux"},
				ReducedCIMode: distmatrix.ReducedCIEnabled,
			},
			expected: map[string][]string{
				"linux": {"linux_amd64"},
			},
		},
		{
			name: "opt in entries require explicit opt in list",
			opts: distmatrix.ComputeOptions{
				Platforms:     []string{"linux", "windows"},
				ArchTokens:    []string{"amd64", "arm64"},
				OptInArchs:    []string{"windows_arm64"},
				ReducedCIMode: distmatrix.ReducedCIDisabled,
			},
			expected: map[string][]string{
				"linux":   {"linux_amd64", "linux_arm64"},
				"windows": {"windows_amd64", "windows_amd64_mingw", "windows_arm64"},
			},
		},
		{
			name: "empty filtered result keeps include key",
			opts: distmatrix.ComputeOptions{
				Platforms:     []string{"windows"},
				ArchTokens:    []string{"arm64"},
				ReducedCIMode: distmatrix.ReducedCIEnabled,
			},
			expected: map[string][]string{
				"windows": {},
			},
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			got, err := distmatrix.ComputePlatformMatrices(matrix, tc.opts)
			require.NoError(t, err)
			require.Equal(t, len(tc.expected), len(got))

			for platform, expectedArchs := range tc.expected {
				platformMatrix, ok := got[platform]
				require.True(t, ok)
				assert.Equal(t, expectedArchs, extractArchs(platformMatrix.Include))
			}
		})
	}
}

func TestRenderGitHubOutputLines(t *testing.T) {
	t.Parallel()

	matrices := map[string]distmatrix.PlatformMatrix{
		"linux": {
			Include: []distmatrix.Entry{{"duckdb_arch": "linux_amd64"}},
		},
		"windows": {
			Include: []distmatrix.Entry{},
		},
	}

	content, err := distmatrix.RenderGitHubOutputLines(matrices)
	require.NoError(t, err)
	assert.Equal(
		t,
		"linux_matrix={\"include\":[{\"duckdb_arch\":\"linux_amd64\"}]}\nwindows_matrix={\"include\":[]}\n",
		content,
	)
}

func extractArchs(entries []distmatrix.Entry) []string {
	out := make([]string, 0, len(entries))
	for _, entry := range entries {
		arch, _ := entry["duckdb_arch"].(string)
		out = append(out, arch)
	}
	return out
}
