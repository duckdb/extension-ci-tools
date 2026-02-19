package main

import (
	"fmt"
	"log/slog"
	"os"

	"github.com/duckdb/extension-ci-tools/internal/distmatrix"
	"github.com/spf13/cobra"
)

func newMatrixCommand() *cobra.Command {
	var (
		inputPath        string
		platformsRaw     string
		archsRaw         string
		excludeRaw       string
		optInRaw         string
		reducedCIModeRaw string
		outPath          string
	)

	cmd := &cobra.Command{
		Use:   "matrix",
		Short: "Compute distribution matrices and emit GitHub output lines",
		RunE: func(cmd *cobra.Command, _ []string) error {
			if eventPath := os.Getenv("GITHUB_EVENT_PATH"); eventPath != "" {
				slog.Info("Using GitHub event payload file", "event_path", eventPath)
			} else {
				slog.Info("GITHUB_EVENT_PATH is not set so event type is unknown")
			}

			eventType, err := detectGitHubEventTypeFromEnv()
			if err != nil {
				return fmt.Errorf("detect GitHub event type: %w", err)
			}
			slog.Info("Detected GitHub event type", "event_type", eventType)

			reducedCIMode, err := distmatrix.ParseReducedCIMode(reducedCIModeRaw)
			if err != nil {
				return err
			}
			if eventType == githubEventPullRequest && reducedCIMode == distmatrix.ReducedCIAuto {
				reducedCIMode = distmatrix.ReducedCIEnabled
				slog.Info("Enabled reduced CI mode for pull_request event when mode is auto")
			}

			data, err := os.ReadFile(inputPath)
			if err != nil {
				return fmt.Errorf("read input matrix %q: %w", inputPath, err)
			}
			matrix, err := distmatrix.ParseMatrixFile(data)
			if err != nil {
				return fmt.Errorf("parse input matrix %q: %w", inputPath, err)
			}

			result, err := distmatrix.ComputePlatformMatrices(matrix, distmatrix.ComputeOptions{
				Platform:      platformsRaw,
				Arch:          archsRaw,
				Exclude:       excludeRaw,
				OptIn:         optInRaw,
				ReducedCIMode: reducedCIMode,
			})
			if err != nil {
				return fmt.Errorf("compute platform matrices: %w", err)
			}

			content, err := distmatrix.RenderGitHubOutputLines(result, distmatrix.MachineReadable)
			if err != nil {
				return fmt.Errorf("render GitHub output lines: %w", err)
			}
			readable, err := distmatrix.RenderGitHubOutputLines(result, distmatrix.HumanReadable)
			if err != nil {
				return fmt.Errorf("render readable output: %w", err)
			}

			if outPath != "" {
				if err := os.WriteFile(outPath, []byte(content), 0o644); err != nil {
					return fmt.Errorf("write output file %q: %w", outPath, err)
				}
			}

			_, _ = fmt.Fprint(cmd.OutOrStdout(), readable)

			return nil
		},
	}

	cmd.Flags().StringVar(&inputPath, "input", "config/distribution_matrix.json", "Input distribution matrix JSON file")
	cmd.Flags().StringVar(&platformsRaw, "platform", "", "Comma-separated list of platforms")
	cmd.Flags().StringVar(&archsRaw, "arch", "", "Comma-separated list of arch tokens (amd64;arm64)")
	cmd.Flags().StringVar(&excludeRaw, "exclude", "", "Comma-separated list of duckdb_arch values to exclude")
	cmd.Flags().StringVar(&optInRaw, "opt-in", "", "Comma-separated list of opt-in duckdb_arch values")
	cmd.Flags().StringVar(&reducedCIModeRaw, "reduced-ci-mode", "", "Reduced CI mode: auto|enabled|disabled")
	cmd.Flags().StringVar(&outPath, "out", "", "Path to write GitHub output lines")

	return cmd
}
