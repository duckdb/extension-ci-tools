package main

import (
	"github.com/spf13/cobra"
)

func newRootCommand() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "extbuild",
		Short: "DuckDB extension CI helper CLI",
		PersistentPreRun: func(cmd *cobra.Command, _ []string) {
			attachCommandLogger(cmd)
		},
	}
	cmd.AddCommand(newMatrixCommand())
	return cmd
}
