#!/usr/bin/env python3
"""Deploy every unique extension binary found in downloaded group artifacts."""

from __future__ import annotations

import argparse
from pathlib import Path, PurePath
import shutil
import subprocess
import tempfile


def extension_name(path: Path) -> str:
    wasm_suffix = ".duckdb_extension.wasm"
    native_suffix = ".duckdb_extension"
    if path.name.endswith(wasm_suffix):
        return path.name[: -len(wasm_suffix)]
    if path.name.endswith(native_suffix):
        return path.name[: -len(native_suffix)]
    raise ValueError(f"not a DuckDB extension binary: {path}")


def discover_extensions(root: Path) -> list[tuple[str, Path]]:
    candidates = sorted(
        path
        for path in root.rglob("*.duckdb_extension*")
        if path.is_file()
        and (
            path.name.endswith(".duckdb_extension")
            or path.name.endswith(".duckdb_extension.wasm")
        )
    )
    selected: dict[str, Path] = {}
    for path in candidates:
        name = extension_name(path)
        if name in selected:
            print(
                f"warning: skipping duplicate extension {name!r}: {path} "
                f"(using {selected[name]})"
            )
            continue
        selected[name] = path
    return list(selected.items())


def deploy_extensions(
    root: Path,
    script: PurePath,
    extension_version: str,
    duckdb_version: str,
    architecture: str,
    bucket: str,
    deploy_latest: bool,
    deploy_versioned: bool,
) -> None:
    extensions = discover_extensions(root)
    if not extensions:
        raise FileNotFoundError(f"no extension binaries found below {root}")
    with tempfile.TemporaryDirectory(prefix="duckdb-extension-deploy-") as directory:
        staging = Path(directory)
        for name, source in extensions:
            destination = staging / source.name
            shutil.copy2(source, destination)
            subprocess.run(
                [
                    "bash",
                    script.as_posix(),
                    name,
                    extension_version,
                    duckdb_version,
                    architecture,
                    bucket,
                    str(deploy_latest).lower(),
                    str(deploy_versioned).lower(),
                    staging.as_posix(),
                ],
                check=True,
            )
            destination.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--extension-version", required=True)
    parser.add_argument("--duckdb-version", required=True)
    parser.add_argument("--architecture", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--latest", action="store_true")
    parser.add_argument("--versioned", action="store_true")
    args = parser.parse_args()
    deploy_extensions(
        args.root,
        args.script,
        args.extension_version,
        args.duckdb_version,
        args.architecture,
        args.bucket,
        args.latest,
        args.versioned,
    )


if __name__ == "__main__":
    main()
