package distmatrix

import (
	"bytes"
	"cmp"
	"encoding/json"
	"errors"
	"fmt"
	"io"
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

type Entry struct {
	DuckDBArch   string  `json:"duckdb_arch"`
	Runner       *string `json:"runner"`
	OSXBuildArch *string `json:"osx_build_arch"`

	VCPKGTargetTriplet string `json:"vcpkg_target_triplet"`
	VCPKGHostTriplet   string `json:"vcpkg_host_triplet"`
	RunInReducedCIMode bool   `json:"run_in_reduced_ci_mode"`
	OptIn              bool   `json:"opt_in"`
}

type PlatformMatrix struct {
	Include []PlatformOutput `json:"include,omitempty"`
}

type PlatformOutput struct {
	DuckDBArch   string  `json:"duckdb_arch"`
	Runner       string  `json:"runner,omitempty"`
	OSXBuildArch *string `json:"osx_build_arch,omitempty"`

	VCPKGTargetTriplet string `json:"vcpkg_target_triplet,omitempty"`
	VCPKGHostTriplet   string `json:"vcpkg_host_triplet,omitempty"`
}

type ReducedCIMode string

const (
	ReducedCIAuto     ReducedCIMode = "auto"
	ReducedCIEnabled  ReducedCIMode = "enabled"
	ReducedCIDisabled ReducedCIMode = "disabled"
)

type ComputeOptions struct {
	Platform      string
	Arch          string
	Exclude       string
	OptIn         string
	ReducedCIMode ReducedCIMode
	RunnerJSON    string
}

type RunnerOverrides map[string]string

func ParseMatrixFile(data []byte) (MatrixFile, error) {
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()

	var matrix MatrixFile
	if err := decoder.Decode(&matrix); err != nil {
		return nil, err
	}
	if err := decoder.Decode(new(struct{})); err != io.EOF {
		return nil, errors.New("invalid JSON: multiple top-level values")
	}
	return matrix, nil
}

func ComputePlatformMatrices(matrix MatrixFile, opts ComputeOptions) (map[string]PlatformMatrix, error) {
	runnerOverrides, err := ParseRunnerOverrides(opts.RunnerJSON)
	if err != nil {
		return nil, err
	}

	platforms := splitList(opts.Platform)
	if len(platforms) == 0 {
		platforms = sortedMatrixPlatforms(matrix)
		if len(platforms) == 0 {
			return nil, errors.New("at least one platform must be provided")
		}
	} else {
		var err error
		platforms, err = normalizePlatforms(platforms)
		if err != nil {
			return nil, err
		}
	}

	archTokens, err := normalizeArchTokens(splitList(opts.Arch))
	if err != nil {
		return nil, err
	}

	parsedReducedCIMode, err := ParseReducedCIMode(string(opts.ReducedCIMode))
	if err != nil {
		return nil, err
	}
	reducedCI := parsedReducedCIMode == ReducedCIEnabled

	optInSet := toSet(splitList(opts.OptIn))
	excludedSet := toSet(splitList(opts.Exclude))
	results := make(map[string]PlatformMatrix, len(platforms))

	for _, platform := range platforms {
		cfg, ok := matrix[platform]
		if !ok {
			return nil, fmt.Errorf("unknown platform: %s", platform)
		}

		filtered := make([]PlatformOutput, 0, len(cfg.Include))
		for _, entry := range cfg.Include {
			if includeEntry(entry, archTokens, excludedSet, reducedCI, optInSet) {
				output := toPlatformOutput(entry)
				if override, ok := runnerOverrides.lookup(platform, entry.DuckDBArch); ok {
					output.Runner = override
				}
				filtered = append(filtered, output)
			}
		}

		slices.SortFunc(filtered, func(a, b PlatformOutput) int {
			return cmp.Compare(a.DuckDBArch, b.DuckDBArch)
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

func sortedMatrixPlatforms(m MatrixFile) []string {
	platforms := make([]string, 0, len(m))
	for platform := range m {
		platforms = append(platforms, platform)
	}
	slices.Sort(platforms)
	return platforms
}

func includeEntry(entry Entry, archTokens map[string]struct{}, excludedSet map[string]struct{}, reducedCI bool, optInSet map[string]struct{}) bool {
	duckdbArch := entry.DuckDBArch
	if duckdbArch == "" {
		return false
	}

	if _, excluded := excludedSet[duckdbArch]; excluded {
		return false
	}

	if len(archTokens) > 0 && !matchesArchToken(duckdbArch, archTokens) {
		return false
	}

	if reducedCI && !entry.RunInReducedCIMode {
		return false
	}

	if entry.OptIn {
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

func ParseReducedCIMode(mode string) (ReducedCIMode, error) {
	switch mode {
	case "", string(ReducedCIAuto):
		return ReducedCIAuto, nil
	case string(ReducedCIEnabled):
		return ReducedCIEnabled, nil
	case string(ReducedCIDisabled):
		return ReducedCIDisabled, nil
	default:
		return "", fmt.Errorf("invalid reduced CI mode: %q (must be auto|enabled|disabled)", mode)
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

func ParseRunnerOverrides(raw string) (RunnerOverrides, error) {
	if strings.TrimSpace(raw) == "" {
		return RunnerOverrides{}, nil
	}

	decoder := json.NewDecoder(strings.NewReader(raw))
	decoder.DisallowUnknownFields()

	var overrides map[string]string
	if err := decoder.Decode(&overrides); err != nil {
		return nil, fmt.Errorf("parse runner overrides: %w", err)
	}
	if err := decoder.Decode(new(struct{})); err != io.EOF {
		return nil, errors.New("parse runner overrides: invalid JSON: multiple top-level values")
	}

	result := make(RunnerOverrides, len(overrides))
	for key, value := range overrides {
		key = strings.TrimSpace(key)
		value = strings.TrimSpace(value)
		if key == "" {
			return nil, errors.New("parse runner overrides: override key cannot be empty")
		}
		if value == "" {
			return nil, fmt.Errorf("parse runner overrides: override value for %q cannot be empty", key)
		}
		result[key] = value
	}

	return result, nil
}

func toPlatformOutput(entry Entry) PlatformOutput {
	runner := ""
	if entry.Runner != nil {
		runner = *entry.Runner
	}

	return PlatformOutput{
		DuckDBArch:         entry.DuckDBArch,
		Runner:             runner,
		OSXBuildArch:       entry.OSXBuildArch,
		VCPKGTargetTriplet: entry.VCPKGTargetTriplet,
		VCPKGHostTriplet:   entry.VCPKGHostTriplet,
	}
}

func (o RunnerOverrides) lookup(platform string, duckdbArch string) (string, bool) {
	if len(o) == 0 {
		return "", false
	}

	if override, ok := o[duckdbArch]; ok {
		return override, true
	}

	for _, key := range runnerOverrideAliases(platform, duckdbArch) {
		if override, ok := o[key]; ok {
			return override, true
		}
	}

	return "", false
}

func runnerOverrideAliases(platform string, duckdbArch string) []string {
	if platform != "linux" {
		return nil
	}

	switch duckdbArch {
	case "linux_amd64", "linux_amd64_musl":
		return []string{"linux_x64"}
	case "linux_arm64", "linux_arm64_musl":
		return []string{"linux_arm64"}
	default:
		return nil
	}
}
