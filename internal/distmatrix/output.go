package distmatrix

import (
	"encoding/json"
	"fmt"
	"strings"
)

func RenderGitHubOutputLines(matrices map[string]PlatformMatrix) (string, error) {
	var b strings.Builder
	orderedPlatforms := sortedPlatforms(matrices)
	for _, platform := range orderedPlatforms {
		matrix, ok := matrices[platform]
		if !ok {
			return "", fmt.Errorf("missing matrix for platform: %s", platform)
		}
		payload, err := json.Marshal(matrix)
		if err != nil {
			return "", err
		}
		_, _ = b.WriteString(platform)
		_, _ = b.WriteString("_matrix=")
		_, _ = b.Write(payload)
		_, _ = b.WriteString("\n")
	}
	return b.String(), nil
}

func SplitSemicolonList(raw string) []string {
	if strings.TrimSpace(raw) == "" {
		return nil
	}
	parts := strings.Split(raw, ";")
	out := make([]string, 0, len(parts))
	for _, part := range parts {
		part = strings.TrimSpace(part)
		if part != "" {
			out = append(out, part)
		}
	}
	return out
}
