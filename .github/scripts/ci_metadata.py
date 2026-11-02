"""Resolve build identity from Cargo, vendored TDLib, and native-build.json."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def metadata():
    workspace = json.loads(subprocess.check_output(
        ["cargo", "metadata", "--no-deps", "--format-version", "1"], cwd=ROOT,
        text=True,
    ))
    package = next(p for p in workspace["packages"] if p["name"] == "tdlib-rs")
    version = package["metadata"]["system-deps"]["tdjson"]
    cmake = (ROOT / "td/CMakeLists.txt").read_text()
    native_version = re.search(r"project\(TDLib VERSION ([\d.]+)\b", cmake)[1]
    if version != native_version:
        raise ValueError(f"Cargo TDLib {version} != vendored TDLib {native_version}")
    repository = package["repository"].removeprefix("https://github.com/").removesuffix(".git")
    if repository == package["repository"] or len(repository.split("/")) != 2:
        raise ValueError("package.repository must identify a GitHub repository")
    config = json.loads((ROOT / ".github/native-build.json").read_text())
    values = {k.upper(): str(v) for k, v in config.items() if isinstance(v, (str, int))}
    values.update(
        CRATE_VERSION=package["version"], TDLIB_VERSION=version,
        CARGO_TARGET_DIR=workspace["target_directory"],
        RUNNER_IMAGE=f'{os.getenv("ImageOS", "unknown")}-{os.getenv("ImageVersion", "unknown")}',
        RELEASE_TAG=f'v{package["version"]}', RELEASE_REPOSITORY=repository,
        TD_TREE=subprocess.check_output(["git", "rev-parse", "HEAD:td"], cwd=ROOT, text=True).strip(),
    )
    # Both source and build recipe must match before reusing an installed TDLib.
    recipe = hashlib.sha256()
    paths = [ROOT / ".github/native-build.json", ROOT / ".github/workflows/build-tdlib.yml",
             ROOT / ".github/scripts/relocate_pkgconfig.py",
             ROOT / "tdlib-rs/native-libraries.json", Path(__file__)]
    for path in paths:
        recipe.update(path.read_bytes())
    values["BUILD_RECIPE"] = recipe.hexdigest()
    matrices = {k: json.dumps(v) for k, v in config["runners"].items()}
    matrices["desktop"] = json.dumps([r for runners in config["runners"].values() for r in runners])
    matrices["android"] = json.dumps(config["architectures"])
    return values, matrices


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--os", choices=("linux", "macos", "windows", "android"))
    parser.add_argument("--arch", choices=("aarch64", "x86_64"))
    args = parser.parse_args()
    values, matrices = metadata()
    target_os = args.os or {"Linux": "linux", "macOS": "macos", "Windows": "windows"}.get(os.getenv("RUNNER_OS"))
    arch = args.arch or {"X64": "x86_64", "ARM64": "aarch64"}.get(os.getenv("RUNNER_ARCH"))
    if target_os and arch:
        values["TDLIB_ARTIFACT"] = f'tdlib-{values["TDLIB_VERSION"]}-{target_os}-{arch}'
    for destination, entries in (("GITHUB_ENV", values), ("GITHUB_OUTPUT", {**values, **matrices})):
        if os.getenv(destination):
            with open(os.environ[destination], "a", encoding="utf-8") as output:
                for key, value in entries.items():
                    print(f"{key}={value}", file=output)
    print(json.dumps({**values, **matrices}, indent=2))


if __name__ == "__main__":
    main()
