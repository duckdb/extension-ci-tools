package main

import (
	"fmt"
	"os"

	"github.com/duckdb/extension-ci-tools/internal/distmatrix"
	"github.com/spf13/cobra"
)

func newMatrixCommand() *cobra.Command {
	var (
		inputPath     string
		platformsRaw  string
		archsRaw      string
		optInRaw      string
		reducedCIMode string
		outPath       string
	)

	cmd := &cobra.Command{
		Use:   "matrix",
		Short: "Compute distribution matrices and emit GitHub output lines",
		RunE: func(_ *cobra.Command, _ []string) error {
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
				OptIn:         optInRaw,
				ReducedCIMode: distmatrix.ReducedCIMode(reducedCIMode),
			})
			if err != nil {
				return fmt.Errorf("compute platform matrices: %w", err)
			}

			content, err := distmatrix.RenderGitHubOutputLines(result)
			if err != nil {
				return fmt.Errorf("render GitHub output lines: %w", err)
			}

			if err := os.WriteFile(outPath, []byte(content), 0o644); err != nil {
				return fmt.Errorf("write output file %q: %w", outPath, err)
			}

			return nil
		},
	}

	cmd.Flags().StringVar(&inputPath, "input", "config/distribution_matrix.json", "Input distribution matrix JSON file")
	cmd.Flags().StringVar(&platformsRaw, "platform", "", "Semicolon-separated list of platforms")
	cmd.Flags().StringVar(&archsRaw, "arch", "", "Semicolon-separated list of arch tokens (amd64;arm64)")
	cmd.Flags().StringVar(&optInRaw, "opt-in", "", "Semicolon-separated list of opt-in duckdb_arch values")
	cmd.Flags().StringVar(&reducedCIMode, "reduced-ci-mode", "", "Reduced CI mode: auto|enabled|disabled")
	cmd.Flags().StringVar(&outPath, "out", "", "Path to write GitHub output lines")

	must(cmd.MarkFlagRequired("platform"))
	must(cmd.MarkFlagRequired("reduced-ci-mode"))
	must(cmd.MarkFlagRequired("out"))

	return cmd
}

func must(err error) {
	if err != nil {
		panic(err)
	}
}
