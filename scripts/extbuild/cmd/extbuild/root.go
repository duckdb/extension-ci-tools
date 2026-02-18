package main

import "github.com/spf13/cobra"

func newRootCommand() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "extbuild",
		Short: "DuckDB extension CI helper CLI",
	}
	cmd.AddCommand(newMatrixCommand())
	return cmd
}
