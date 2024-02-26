# Extension CI Tools for DuckDB
This repository contains reusable components for Github Actions based CI for building, testing and deploying DuckDB extensions.

By using a [Reusable Workflow](https://docs.github.com/en/actions/using-workflows/reusing-workflows) or Action from this repository in a DuckDB extension project, an extension repository can receive updates to its CI automatically.

## Versioning
| Extension-ci-tools Branch  | DuckDB target version | Actively maintained? |
|---------|-----------------------|----------------------|
| main    | main                  | yes                  |
| v0.10.0 | v0.10.0               | yes                  |

Each branch in this repository targets a specific version of DuckDB. Note that these branches will be continually updated to ensure the build environment is functional for that version of DuckDB.
Also note that at some point, support for versions will be dropped. Currently, we aim to support the latest 2 DuckDB versions, to allow extensions devs to transition to a new DuckDB version.
