# Extension CI Tools for DuckDB

This repository contains reusable components for building, testing and deploying DuckDB extensions.

DuckDB's [Extension Template](https://github.com/duckdb/extension-template) and various DuckDB extensions use this repository to share build configuration and CI workflows.

## Usage examples

Add this repository to an extension as the `extension-ci-tools` submodule. After cloning the extension, initialize its submodules:

```shell
git submodule update --init --recursive
```

When the extension has a `duckdb` submodule, omit `duckdb_version` from the distribution and deployment workflows. CI uses the commit pinned by the submodule.

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
      ci_tools_version: main
```

#### Shared extension sources and project configuration

With a DuckDB revision that supports the new sync options, enable shared extension
checkouts before including `duckdb_extension.Makefile`:

```make
DUCKDB_NEW_EXTENSION_BUILD := 1
BUILD_EXTENSIONS := httpfs;avro;aws
EXTENSION_CONFIG_BASE_DIR := $(PROJ_DIR)extension-configs
EXTRA_EXTENSION_CONFIGS := $(PROJ_DIR)extension_overrides.cmake
```

The wrapper passes the same extension selection, config list, and config directory
to DuckDB's sync script and CMake. Sync populates `duckdb/extension/external/` and
writes the combined vcpkg manifest to the extension project's `build/vcpkg.json`
before configuration. New mode takes precedence over `USE_MERGED_VCPKG_MANIFEST`,
including for WebAssembly targets. The previous build flow remains available when
`DUCKDB_NEW_EXTENSION_BUILD` is unset.

`BUILD_EXTENSIONS` selects named extensions. `DUCKDB_EXTENSIONS` is an alias that
takes precedence when set. Legacy `CORE_EXTENSIONS` entries and the dependencies
selected by `BUILD_EXTENSION_TEST_DEPS` are added to that list; they do not need
to be exported by the extension's Makefile.

`EXTRA_EXTENSION_CONFIGS` is prepended to the project's `EXT_CONFIG`. Alternatively,
set `EXTENSION_CONFIGS` to supply the complete semicolon-separated config list.
Include the project's own config in that list so its extension and vcpkg dependencies
are retained. With the updated DuckDB loader, explicit configs take precedence over
named defaults, and the first declaration of an extension wins. Use these configs
to control `GIT_URL` and literal `GIT_TAG` hashes.

`EXTENSION_CONFIG_BASE_DIR` is optional. It replaces DuckDB's default directory of
`<extension>.cmake` files for named lookups. Use absolute config paths, as in the
example: relative paths are resolved from the DuckDB source directory, not the
extension project. These settings configure extension revisions, not vcpkg registry
baselines.

### Rust

[duckdb-delta](https://github.com/duckdb/duckdb-delta/) uses the standard extension makefile and enables the Rust toolchain in CI:

```make
PROJ_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

EXT_NAME=delta
EXT_CONFIG=${PROJ_DIR}extension_config.cmake

include extension-ci-tools/makefiles/duckdb_extension.Makefile
```

```yaml
jobs:
  duckdb-stable-build:
    uses: duckdb/extension-ci-tools/.github/workflows/_extension_distribution.yml@main
    with:
      extension_name: delta
      ci_tools_version: main
      enable_rust: true
```

### C API

[odbc-scanner](https://github.com/duckdb/odbc-scanner/) does not have a `duckdb` submodule, so it sets the DuckDB version explicitly. It uses the C API makefiles. Set `USE_UNSTABLE_C_API` to `1` only when the extension needs DuckDB's unstable C API.

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
