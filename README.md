# Extension CI Tools for DuckDB

This repository contains reusable components for building, testing and deploying DuckDB extensions.

DuckDB's [Extension Template](https://github.com/duckdb/extension-template) and various DuckDB extensions use this repository to share build configuration and CI workflows.

## Usage examples

Add this repository to an extension as the `extension-ci-tools` submodule. After cloning the extension, initialize its submodules:

```shell
git submodule update --init --recursive
```

### C++

[duckdb-httpfs](https://github.com/duckdb/duckdb-httpfs/) includes the standard extension and vcpkg makefiles:

```make
PROJ_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

EXT_NAME=httpfs
EXT_CONFIG=${PROJ_DIR}extension_config.cmake

include extension-ci-tools/makefiles/duckdb_extension.Makefile
include extension-ci-tools/makefiles/vcpkg.Makefile
```

It calls the reusable distribution workflow to build and test the extension on supported platforms:

```yaml
jobs:
  duckdb-stable-build:
    uses: duckdb/extension-ci-tools/.github/workflows/_extension_distribution.yml@main
    with:
      extension_name: httpfs
      duckdb_version: main
      ci_tools_version: main
```

### Rust

[duckdb-delta](https://github.com/duckdb/duckdb-delta/) uses the standard extension makefile and enables the Rust toolchain in CI:

```make
PROJ_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

EXT_NAME=deltatable
EXT_CONFIG=${PROJ_DIR}extension_config.cmake

include extension-ci-tools/makefiles/duckdb_extension.Makefile
```

```yaml
jobs:
  duckdb-stable-build:
    uses: duckdb/extension-ci-tools/.github/workflows/_extension_distribution.yml@main
    with:
      extension_name: delta
      duckdb_version: main
      ci_tools_version: main
      enable_rust: true
```

### C API

[odbc-scanner](https://github.com/duckdb/odbc-scanner/) uses the C API makefiles. Set `USE_UNSTABLE_C_API` to `1` only when the extension needs DuckDB's unstable C API.

```make
PROJ_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

EXTENSION_NAME := odbc_scanner
USE_UNSTABLE_C_API := 0
TARGET_DUCKDB_VERSION := <duckdb-version>

include extension-ci-tools/makefiles/c_api_extensions/base.Makefile
include extension-ci-tools/makefiles/c_api_extensions/c_cpp.Makefile
```

Its CI adds the tools needed by the extension:

```yaml
jobs:
  duckdb-build:
    uses: duckdb/extension-ci-tools/.github/workflows/_extension_distribution.yml@main
    with:
      extension_name: odbc_scanner
      duckdb_version: <duckdb-version>
      ci_tools_version: main
      extra_toolchains: python3;unixodbc;
      build_duckdb_shell: false
```

The shared makefiles provide common targets such as `make debug`, `make test_debug` and `make release`.
