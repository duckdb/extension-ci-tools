package distmatrix

import (
	"encoding/json"
	"fmt"
	"strings"
)

type OutputMode string

const (
	MachineReadable OutputMode = "machine"
	HumanReadable   OutputMode = "human"
)

func RenderGitHubOutputLines(matrices map[string]PlatformMatrix, mode OutputMode) (string, error) {
	marshall := func(matrix PlatformMatrix) ([]byte, error) {
		if mode == MachineReadable {
			return json.Marshal(matrix)
		} else {
			return json.MarshalIndent(matrix, "", "  ")
		}
	}

	var b strings.Builder
	orderedPlatforms := sortedPlatforms(matrices)
	for _, platform := range orderedPlatforms {
		matrix, ok := matrices[platform]
		if !ok {
			return "", fmt.Errorf("missing matrix for platform: %s", platform)
		}
		payload, err := marshall(matrix)
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

func splitList(raw string) []string {
	if strings.TrimSpace(raw) == "" {
		return nil
	}
	parts := strings.FieldsFunc(raw, func(r rune) bool {
		return r == ';' || r == ','
	})
	out := make([]string, 0, len(parts))
	for _, part := range parts {
		part = strings.TrimSpace(part)
		if part != "" {
			out = append(out, part)
		}
	}
	return out
}
