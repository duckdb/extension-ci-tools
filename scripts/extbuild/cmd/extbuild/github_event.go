package main

import (
	"encoding/json"
	"fmt"
	"os"
)

const (
	githubEventUnknown     = "unknown"
	githubEventPullRequest = "pull_request"
	githubEventPush        = "push"
)

func detectGitHubEventTypeFromEnv() (string, error) {
	eventPath := os.Getenv("GITHUB_EVENT_PATH")
	if eventPath == "" {
		return githubEventUnknown, nil
	}

	eventType, err := detectGitHubEventTypeFromFile(eventPath)
	if err != nil {
		return "", fmt.Errorf("read GitHub event %q: %w", eventPath, err)
	}

	return eventType, nil
}

func detectGitHubEventTypeFromFile(path string) (string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}

	var payload map[string]json.RawMessage
	if err := json.Unmarshal(data, &payload); err != nil {
		return "", err
	}

	if _, ok := payload[githubEventPullRequest]; ok {
		return githubEventPullRequest, nil
	}
	if _, ok := payload["ref"]; ok {
		return githubEventPush, nil
	}

	return githubEventUnknown, nil
}
