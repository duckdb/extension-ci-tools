package main

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestDetectGitHubEventTypeFromEnv(t *testing.T) {
	tests := []struct {
		name      string
		eventPath string
		want      string
		wantErr   bool
	}{
		{
			name: "missing env var returns unknown",
			want: githubEventUnknown,
		},
		{
			name:      "pull request fixture",
			eventPath: fixturePath(t, "extension_template_pull_request.json"),
			want:      githubEventPullRequest,
		},
		{
			name:      "push fixture",
			eventPath: fixturePath(t, "extension_template_push.json"),
			want:      githubEventPush,
		},
		{
			name:      "unknown fixture",
			eventPath: fixturePath(t, "extension_template_unknown.json"),
			want:      githubEventUnknown,
		},
		{
			name:      "missing file returns error",
			eventPath: filepath.Join(t.TempDir(), "missing.json"),
			wantErr:   true,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if tc.eventPath == "" {
				t.Setenv("GITHUB_EVENT_PATH", "")
			} else {
				t.Setenv("GITHUB_EVENT_PATH", tc.eventPath)
			}

			got, err := detectGitHubEventTypeFromEnv()
			if tc.wantErr {
				require.Error(t, err)
				return
			}

			require.NoError(t, err)
			assert.Equal(t, tc.want, got)
		})
	}
}

func TestDetectGitHubEventTypeFromEnvInvalidJSON(t *testing.T) {
	tmpDir := t.TempDir()
	invalidPath := filepath.Join(tmpDir, "invalid.json")
	require.NoError(t, os.WriteFile(invalidPath, []byte("{"), 0o600))

	t.Setenv("GITHUB_EVENT_PATH", invalidPath)
	_, err := detectGitHubEventTypeFromEnv()
	require.Error(t, err)
}

func TestMatrixSubcommandLogsDetectedEventType(t *testing.T) {
	eventPath := fixturePath(t, "extension_template_pull_request.json")
	t.Setenv("GITHUB_EVENT_PATH", eventPath)
	_, stderr, err := executeRootCommandWithResult(t, []string{
		"matrix",
		"--input", matrixConfigPath(t),
		"--platform", "linux",
	})
	require.NoError(t, err)
	assert.Contains(t, stderr, "\x1b[90m")
	assert.Contains(t, stderr, "\x1b[34mINF\x1b[0m")
	assert.Contains(t, stderr, "Using GitHub event payload file")
	assert.Contains(t, stderr, "event_path="+eventPath)
	assert.Contains(t, stderr, "Detected GitHub event type")
	assert.Contains(t, stderr, "event_type=pull_request")
}

func TestMatrixSubcommandFailsWhenEventPathInvalid(t *testing.T) {
	t.Setenv("GITHUB_EVENT_PATH", filepath.Join(t.TempDir(), "missing.json"))
	_, _, err := executeRootCommandWithResult(t, []string{
		"matrix",
		"--input", matrixConfigPath(t),
		"--platform", "linux",
	})
	require.Error(t, err)
	assert.ErrorContains(t, err, "detect GitHub event type")
}

func TestMatrixSubcommandPullRequestEnablesReducedCIWhenAuto(t *testing.T) {
	t.Setenv("GITHUB_EVENT_PATH", fixturePath(t, "extension_template_pull_request.json"))

	inputJSON := `{
  "linux": {
    "include": [
      {"duckdb_arch":"linux_amd64","run_in_reduced_ci_mode":true,"opt_in":false},
      {"duckdb_arch":"linux_arm64","run_in_reduced_ci_mode":false,"opt_in":false}
    ]
  }
}`

	outputPath, _ := runMatrixCommand(t, inputJSON, []string{
		"--platform", "linux",
		"--reduced-ci-mode", "auto",
	})

	out, err := os.ReadFile(outputPath)
	require.NoError(t, err)
	assert.Contains(t, string(out), "linux_amd64")
	assert.NotContains(t, string(out), "linux_arm64")
}

func fixturePath(t *testing.T, name string) string {
	t.Helper()
	return filepath.Join(moduleRootPath(t), "testdata", "github", "events", name)
}

func matrixConfigPath(t *testing.T) string {
	t.Helper()
	return filepath.Join(moduleRootPath(t), "..", "..", "config", "distribution_matrix.json")
}

func moduleRootPath(t *testing.T) string {
	t.Helper()
	wd, err := os.Getwd()
	require.NoError(t, err)
	return filepath.Clean(filepath.Join(wd, "..", ".."))
}
