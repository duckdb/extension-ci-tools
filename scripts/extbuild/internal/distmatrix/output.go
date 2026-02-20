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

type DeployOutput struct {
	Include []DeployOutputEntry `json:"include,omitempty"`
}

type DeployOutputEntry struct {
	DuckDBArch string `json:"duckdb_arch"`
}

func RenderDeployGitHubOutputLine(matrices map[string]PlatformMatrix) (string, error) {
	deploy := buildDeployOutput(matrices)
	payload, err := json.Marshal(deploy)
	if err != nil {
		return "", err
	}
	return "deploy_matrix=" + string(payload) + "\n", nil
}

func RenderDeployReadableLines(matrices map[string]PlatformMatrix) string {
	deploy := buildDeployOutput(matrices)
	var b strings.Builder
	for _, entry := range deploy.Include {
		_, _ = b.WriteString(entry.DuckDBArch)
		_, _ = b.WriteString("\n")
	}
	return b.String()
}

func buildDeployOutput(matrices map[string]PlatformMatrix) DeployOutput {
	orderedPlatforms := sortedPlatforms(matrices)
	include := make([]DeployOutputEntry, 0)
	for _, platform := range orderedPlatforms {
		matrix, ok := matrices[platform]
		if !ok {
			continue
		}
		for _, entry := range matrix.Include {
			include = append(include, DeployOutputEntry{DuckDBArch: entry.DuckDBArch})
		}
	}
	return DeployOutput{Include: include}
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
