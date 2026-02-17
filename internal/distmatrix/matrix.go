package distmatrix

import (
	"cmp"
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"strings"
)

var validArchTokens = map[string]struct{}{
	"amd64": {},
	"arm64": {},
}

type MatrixFile map[string]PlatformConfig

type PlatformConfig struct {
	Include []Entry `json:"include"`
}

type Entry map[string]any

type PlatformMatrix struct {
	Include []Entry `json:"include"`
}

type ReducedCIMode string

const (
	ReducedCIAuto     ReducedCIMode = "auto"
	ReducedCIEnabled  ReducedCIMode = "enabled"
	ReducedCIDisabled ReducedCIMode = "disabled"
)

type ComputeOptions struct {
	Platforms     []string
	ArchTokens    []string
	OptInArchs    []string
	ReducedCIMode ReducedCIMode
}

func ParseMatrixFile(data []byte) (MatrixFile, error) {
	var matrix MatrixFile
	if err := json.Unmarshal(data, &matrix); err != nil {
		return nil, err
	}
	return matrix, nil
}

func ComputePlatformMatrices(matrix MatrixFile, opts ComputeOptions) (map[string]PlatformMatrix, error) {
	platforms, err := normalizePlatforms(opts.Platforms)
	if err != nil {
		return nil, err
	}

	archTokens, err := normalizeArchTokens(opts.ArchTokens)
	if err != nil {
		return nil, err
	}

	reducedCI, err := parseReducedCIMode(opts.ReducedCIMode)
	if err != nil {
		return nil, err
	}

	optInSet := toSet(opts.OptInArchs)
	results := make(map[string]PlatformMatrix, len(platforms))

	for _, platform := range platforms {
		cfg, ok := matrix[platform]
		if !ok {
			return nil, fmt.Errorf("unknown platform: %s", platform)
		}

		filtered := make([]Entry, 0, len(cfg.Include))
		for _, entry := range cfg.Include {
			if includeEntry(entry, archTokens, reducedCI, optInSet) {
				filtered = append(filtered, cloneEntry(entry))
			}
		}

		slices.SortFunc(filtered, func(a, b Entry) int {
			return cmp.Compare(getString(a, "duckdb_arch"), getString(b, "duckdb_arch"))
		})

		results[platform] = PlatformMatrix{Include: filtered}
	}

	return results, nil
}

func sortedPlatforms(m map[string]PlatformMatrix) []string {
	platforms := make([]string, 0, len(m))
	for platform := range m {
		platforms = append(platforms, platform)
	}
	slices.Sort(platforms)
	return platforms
}

func includeEntry(entry Entry, archTokens map[string]struct{}, reducedCI bool, optInSet map[string]struct{}) bool {
	duckdbArch := getString(entry, "duckdb_arch")
	if duckdbArch == "" {
		return false
	}

	if len(archTokens) > 0 && !matchesArchToken(duckdbArch, archTokens) {
		return false
	}

	if reducedCI && !getBool(entry, "run_in_reduced_ci_mode") {
		return false
	}

	if getBool(entry, "opt_in") {
		if _, ok := optInSet[duckdbArch]; !ok {
			return false
		}
	}

	return true
}

func matchesArchToken(duckdbArch string, tokens map[string]struct{}) bool {
	for token := range tokens {
		if strings.Contains(duckdbArch, "_"+token) {
			return true
		}
	}
	return false
}

func parseReducedCIMode(mode ReducedCIMode) (bool, error) {
	switch mode {
	case ReducedCIAuto, ReducedCIDisabled:
		return false, nil
	case ReducedCIEnabled:
		return true, nil
	default:
		return false, fmt.Errorf("invalid reduced CI mode: %q (must be auto|enabled|disabled)", mode)
	}
}

func normalizePlatforms(platforms []string) ([]string, error) {
	clean := normalizeValues(platforms)
	if len(clean) == 0 {
		return nil, errors.New("at least one platform must be provided")
	}
	return clean, nil
}

func normalizeArchTokens(tokens []string) (map[string]struct{}, error) {
	clean := normalizeValues(tokens)
	result := make(map[string]struct{}, len(clean))
	for _, token := range clean {
		if _, ok := validArchTokens[token]; !ok {
			return nil, fmt.Errorf("unknown arch token: %s (supported: amd64, arm64)", token)
		}
		result[token] = struct{}{}
	}
	return result, nil
}

func normalizeValues(values []string) []string {
	seen := map[string]struct{}{}
	out := make([]string, 0, len(values))
	for _, value := range values {
		value = strings.TrimSpace(value)
		if value == "" {
			continue
		}
		if _, ok := seen[value]; ok {
			continue
		}
		seen[value] = struct{}{}
		out = append(out, value)
	}
	return out
}

func toSet(values []string) map[string]struct{} {
	set := make(map[string]struct{}, len(values))
	for _, value := range normalizeValues(values) {
		set[value] = struct{}{}
	}
	return set
}

func getString(entry Entry, key string) string {
	value, ok := entry[key]
	if !ok {
		return ""
	}
	str, ok := value.(string)
	if !ok {
		return ""
	}
	return str
}

func getBool(entry Entry, key string) bool {
	value, ok := entry[key]
	if !ok {
		return false
	}
	flag, ok := value.(bool)
	if !ok {
		return false
	}
	return flag
}

func cloneEntry(entry Entry) Entry {
	cloned := make(Entry, len(entry))
	for key, value := range entry {
		cloned[key] = value
	}
	return cloned
}
