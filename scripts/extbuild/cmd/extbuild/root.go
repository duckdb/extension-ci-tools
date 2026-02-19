package main

import (
	"log/slog"

	"github.com/spf13/cobra"
)

func newRootCommand() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "extbuild",
		Short: "DuckDB extension CI helper CLI",
		PersistentPreRun: func(cmd *cobra.Command, _ []string) {
			slog.SetDefault(newLogger(cmd.ErrOrStderr()))
		},
	}
	cmd.AddCommand(newMatrixCommand())
	return cmd
}
