#!/usr/bin/env python3
"""Deploy every unique extension binary found in downloaded group artifacts."""

from __future__ import annotations

import argparse
from pathlib import Path, PurePath
import shutil
import subprocess
import tempfile


class DeploymentError(RuntimeError):
    """One or more extension deployments failed."""


def extension_name(path: Path) -> str:
    wasm_suffix = ".duckdb_extension.wasm"
    native_suffix = ".duckdb_extension"
    if path.name.endswith(wasm_suffix):
        return path.name[: -len(wasm_suffix)]
    if path.name.endswith(native_suffix):
        return path.name[: -len(native_suffix)]
    raise ValueError(f"not a DuckDB extension binary: {path}")


def extension_architecture(root: Path, path: Path) -> str:
    relative_path = path.relative_to(root)
    if len(relative_path.parts) < 3:
        raise ValueError(
            f"extension binary does not use <version>/<architecture>/<file> layout: "
            f"{path}"
        )
    return relative_path.parent.name


def discover_extensions(root: Path) -> list[tuple[str, str, Path]]:
    candidates = sorted(
        path
        for path in root.rglob("*.duckdb_extension*")
        if path.is_file()
        and (
            path.name.endswith(".duckdb_extension")
            or path.name.endswith(".duckdb_extension.wasm")
        )
    )
    selected: dict[tuple[str, str], Path] = {}
    for path in candidates:
        name = extension_name(path)
        architecture = extension_architecture(root, path)
        key = (architecture, name)
        if key in selected:
            print(
                f"warning: skipping duplicate extension {name!r} for "
                f"{architecture}: {path} (using {selected[key]})"
            )
            continue
        selected[key] = path
    return [
        (architecture, name, path)
        for (architecture, name), path in sorted(selected.items())
    ]


def deploy_extensions(
    root: Path,
    script: PurePath,
    extension_version: str,
    duckdb_version: str,
    bucket: str,
    deploy_latest: bool,
    deploy_versioned: bool,
    requested_extensions: set[str] | None = None,
) -> None:
    extensions = discover_extensions(root)
    if not extensions:
        raise FileNotFoundError(f"no extension binaries found below {root}")
    failures: list[str] = []
    if requested_extensions:
        architectures = sorted({architecture for architecture, _, _ in extensions})
        for architecture in architectures:
            available = {
                name
                for candidate_architecture, name, _ in extensions
                if candidate_architecture == architecture
            }
            missing = sorted(requested_extensions - available)
            if missing:
                failures.append(
                    f"{architecture}: requested extensions not found: "
                    f"{', '.join(missing)}"
                )
        extensions = [
            extension
            for extension in extensions
            if extension[1] in requested_extensions
        ]
    with tempfile.TemporaryDirectory(prefix="duckdb-extension-deploy-") as directory:
        staging = Path(directory)
        for architecture, name, source in extensions:
            destination = staging / source.name
            try:
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
            except subprocess.CalledProcessError as error:
                failures.append(
                    f"{architecture}/{name}: upload command exited with "
                    f"{error.returncode}"
                )
            except OSError as error:
                failures.append(f"{architecture}/{name}: {error}")
            finally:
                destination.unlink(missing_ok=True)
    if failures:
        raise DeploymentError(
            "one or more extension deployments failed:\n- "
            + "\n- ".join(failures)
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--extension-version", required=True)
    parser.add_argument("--duckdb-version", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument(
        "--extensions",
        default="",
        help="Semicolon-separated extension names to deploy; empty deploys all",
    )
    parser.add_argument("--latest", action="store_true")
    parser.add_argument("--versioned", action="store_true")
    args = parser.parse_args()
    requested_extensions = {
        extension.strip()
        for extension in args.extensions.split(";")
        if extension.strip()
    }
    deploy_extensions(
        args.root,
        args.script,
        args.extension_version,
        args.duckdb_version,
        args.bucket,
        args.latest,
        args.versioned,
        requested_extensions,
    )


if __name__ == "__main__":
    main()
