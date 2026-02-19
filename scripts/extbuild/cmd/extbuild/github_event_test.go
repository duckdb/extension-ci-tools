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
			eventPath: fixturePath("extension_template_pull_request.json"),
			want:      githubEventPullRequest,
		},
		{
			name:      "push fixture",
			eventPath: fixturePath("extension_template_push.json"),
			want:      githubEventPush,
		},
		{
			name:      "unknown fixture",
			eventPath: fixturePath("extension_template_unknown.json"),
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
	t.Chdir(filepath.Join("..", "..", "..", ".."))
	t.Setenv("GITHUB_EVENT_PATH", filepath.Join("scripts", "extbuild", "testdata", "github", "events", "extension_template_pull_request.json"))
	_, stderr, err := executeRootCommandWithResult(t, []string{"matrix", "--platform", "linux"})
	require.NoError(t, err)
	assert.Contains(t, stderr, "\x1b[90m")
	assert.Contains(t, stderr, "\x1b[34mINF\x1b[0m")
	assert.Contains(t, stderr, "Detected GitHub event type")
	assert.Contains(t, stderr, "event_type=pull_request")
}

func TestMatrixSubcommandFailsWhenEventPathInvalid(t *testing.T) {
	t.Chdir(filepath.Join("..", "..", "..", ".."))
	t.Setenv("GITHUB_EVENT_PATH", filepath.Join(t.TempDir(), "missing.json"))
	_, _, err := executeRootCommandWithResult(t, []string{"matrix", "--platform", "linux"})
	require.Error(t, err)
	assert.ErrorContains(t, err, "detect GitHub event type")
}

func fixturePath(name string) string {
	return filepath.Join("..", "..", "testdata", "github", "events", name)
}
