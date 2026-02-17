package main

import (
	"bytes"
	"os"
	"path/filepath"
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
      },
      {
        "duckdb_arch": "wasm_threads",
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
				Platform: "linux;windows",
				Arch:     "amd64",
			},
			expected: map[string][]string{
				"linux":   {"linux_amd64"},
				"windows": {"windows_amd64", "windows_amd64_mingw"},
			},
		},
		{
			name: "arch omitted includes all for selected platform",
			opts: distmatrix.ComputeOptions{
				Platform: "wasm",
			},
			expected: map[string][]string{
				"wasm": {"wasm_eh", "wasm_mvp", "wasm_threads"},
			},
		},
		{
			name: "wasm reduced mode enabled includes only mvp",
			opts: distmatrix.ComputeOptions{
				Platform:      "wasm",
				ReducedCIMode: distmatrix.ReducedCIEnabled,
			},
			expected: map[string][]string{
				"wasm": {"wasm_mvp"},
			},
		},
		{
			name: "reduced ci mode enabled",
			opts: distmatrix.ComputeOptions{
				Platform:      "linux",
				ReducedCIMode: distmatrix.ReducedCIEnabled,
			},
			expected: map[string][]string{
				"linux": {"linux_amd64"},
			},
		},
		{
			name: "opt in entries require explicit opt in list",
			opts: distmatrix.ComputeOptions{
				Platform:      "linux;windows",
				Arch:          "amd64;arm64",
				OptIn:         "windows_arm64",
				ReducedCIMode: distmatrix.ReducedCIDisabled,
			},
			expected: map[string][]string{
				"linux":   {"linux_amd64", "linux_arm64"},
				"windows": {"windows_amd64", "windows_amd64_mingw", "windows_arm64"},
			},
		},
		{
			name: "comma separated lists are supported",
			opts: distmatrix.ComputeOptions{
				Platform: "linux,windows",
				Arch:     "amd64,arm64",
			},
			expected: map[string][]string{
				"linux":   {"linux_amd64", "linux_arm64"},
				"windows": {"windows_amd64", "windows_amd64_mingw"},
			},
		},
		{
			name: "empty filtered result keeps include key",
			opts: distmatrix.ComputeOptions{
				Platform:      "windows",
				Arch:          "arm64",
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

func TestMatrixSubcommandWritesOutputFile(t *testing.T) {
	t.Parallel()

	const inputJSON = `{
  "linux": {
    "include": [
      {"duckdb_arch":"linux_amd64","run_in_reduced_ci_mode":true,"opt_in":false},
      {"duckdb_arch":"linux_arm64","run_in_reduced_ci_mode":true,"opt_in":false}
    ]
  },
  "windows": {
    "include": [
      {"duckdb_arch":"windows_amd64","run_in_reduced_ci_mode":true,"opt_in":false}
    ]
  }
}`

	tmpDir := t.TempDir()
	inputPath := filepath.Join(tmpDir, "distribution_matrix.json")
	outputPath := filepath.Join(tmpDir, "matrices.env")

	require.NoError(t, os.WriteFile(inputPath, []byte(inputJSON), 0o600))

	cmd := newRootCommand()
	var stdout bytes.Buffer
	cmd.SetOut(&stdout)
	cmd.SetArgs([]string{
		"matrix",
		"--input", inputPath,
		"--platform", "linux;windows",
		"--arch", "amd64",
		"--reduced-ci-mode", "disabled",
		"--out", outputPath,
	})

	require.NoError(t, cmd.Execute())

	out, err := os.ReadFile(outputPath)
	require.NoError(t, err)

	for _, content := range []string{string(out), stdout.String()} {
		assert.Contains(t, content, "linux_matrix={")
		assert.Contains(t, content, "windows_matrix={")
	}
}

func TestMatrixSubcommandWithoutArgs(t *testing.T) {
	t.Chdir(filepath.Join("..", ".."))

	cmd := newRootCommand()
	var stdout bytes.Buffer
	cmd.SetOut(&stdout)
	cmd.SetArgs([]string{"matrix"})

	require.NoError(t, cmd.Execute())

	output := stdout.String()
	assert.Contains(t, output, "linux_matrix=")
	assert.Contains(t, output, "osx_matrix=")
	assert.Contains(t, output, "windows_matrix=")
	assert.Contains(t, output, "wasm_matrix=")
}

func extractArchs(entries []distmatrix.PlatformOutput) []string {
	out := make([]string, 0, len(entries))
	for _, entry := range entries {
		out = append(out, entry.DuckDBArch)
	}
	return out
}
