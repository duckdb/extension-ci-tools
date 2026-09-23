# Extension CI Tools for DuckDB
This repository contains reusable components for building, testing and deploying DuckDB extensions.

DuckDB's [Extension Template](https://github.com/duckdb/extension-template/actions) and various DuckDB Extensions based on the template use this repository to deduplicate code for build configuration and easily update the extension repositories when changes occur to DuckDB's build system and/or CI.

## Extensions in a subdirectory

The distribution and code-quality workflows accept `extension_directory`, a path
relative to the checked-out repository root. It defaults to `.` for existing callers.
For example, an extension in `extensions/quack/` can use:

```yaml
jobs:
  distribution:
    uses: duckdb/extension-ci-tools/.github/workflows/_extension_distribution.yml@v1.5-variegata
    with:
      extension_name: quack
      extension_directory: extensions/quack
      duckdb_version: v1.5.5
      ci_tools_version: v1.5-variegata

  code-quality:
    uses: duckdb/extension-ci-tools/.github/workflows/_extension_code_quality.yml@v1.5-variegata
    with:
      extension_name: quack
      extension_directory: extensions/quack
      duckdb_version: v1.5.5
      ci_tools_version: v1.5-variegata
```

The selected directory should contain the extension's `Makefile`, build configuration,
and sources, with the same layout as a root-level extension. DuckDB and the CI tools
are checked out under that directory; build commands, tests, quality checks, and
artifact collection use it too. The full repository remains available, including
parent sources and Git metadata inside Linux build containers. Repository checkout,
matrix generation, and compiler caches remain at the workspace root.

With `override_repository`, the directory is relative to that repository. If a
workflow builds multiple extensions, use distinct `artifact_postfix` values to avoid
artifact name collisions. This input does not change the deployment or client-test
workflows.

To run the workflow path regression tests locally, install PyYAML and run
`python -m unittest discover -s tests -v`.

## Versioning
| Extension-ci-tools Branch | DuckDB target version | Actively maintained? |
|---------------------------|-----------------------|----------------------|
| main                      | main                  | yes                  |
| v1.5.4                    | v1.5.4                | yes                  |
| v1.5.3                    | v1.5.3                | no                   |
| v1.5.2                    | v1.5.2                | no                   |
| v1.5.1                    | v1.5.1                | no                   |
| v1.5.0                    | v1.5.0                | no                   |
| v1.4.5                    | v1.4.5                | yes                  |
| v1.4.4                    | v1.4.4                | no                   |
| v1.4.3                    | v1.4.3                | no                   |
| v1.4.2                    | v1.4.2                | no                   |
| v1.4.1                    | v1.4.1                | no                   |
| v1.4.0                    | v1.4.0                | no                   |
| v1.3.2                    | v1.3.2                | no                   |
| v1.3.1                    | v1.3.1                | no                   |
| v1.3.0                    | v1.3.0                | no                   |
| <= v1.2.2                 |                       | no                   |

Each branch in this repository targets a specific version of DuckDB. Note that these branches will be continually updated to ensure the build environment is functional for that version of DuckDB.
Also note that at some point, support for versions will be dropped. Currently, we aim to support the latest 2 DuckDB versions, to allow extensions devs to transition to a new DuckDB version.
