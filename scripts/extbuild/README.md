# Extbuild

A small CLI tool for iterating quickly on DuckDB extension CI pipelines.

For now, it can compute the job matrix. Later, the tool can be extended to
generate the command list used.

# Development

## Install dependencies

```shell
brew install go
```

## Build extbuild

Build and test `extbuild`:

```shell
make build test -sj4
```

or from the repo root directory:

```shell
make -C scripts/extbuild build test -sj4
```

## How to use

The target `build` creates `./scripts/extbuild/build/extbuild`.

See `./scripts/extbuild/build/extbuild --help` for local usage and subcommands.
