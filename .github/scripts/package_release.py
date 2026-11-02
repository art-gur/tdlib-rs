"""Package only the complete platform set for this checkout's release."""

import argparse
import json
from pathlib import Path
import zipfile

from ci_metadata import ROOT, metadata


def package(source, destination):
    values, _ = metadata()
    version = values["TDLIB_VERSION"]
    config = json.loads((ROOT / ".github/native-build.json").read_text())
    libraries = json.loads((ROOT / "tdlib-rs/native-libraries.json").read_text())
    platforms = [(os, arch) for os in config["runners"] for arch in config["architectures"]]
    platforms += [("android", arch) for arch in config["architectures"]]
    expected = {f"tdlib-{version}-{os}-{arch}" for os, arch in platforms}
    actual = {p.name for p in source.iterdir() if p.is_dir()}
    if actual != expected:
        raise ValueError(f"release artifact mismatch: missing={expected - actual}, unexpected={actual - expected}")
    # Validate everything before producing archives or creating a GitHub draft.
    for os, arch in platforms:
        directory = source / f"tdlib-{version}-{os}-{arch}"
        shared = {"linux": f"lib/libtdjson.so.{version}",
                  "macos": f"lib/libtdjson.{version}.dylib",
                  "android": "lib/libtdjson.so", "windows": "bin/tdjson.dll"}[os]
        platform = "windows" if os == "windows" else "unix"
        static = [f"lib/{name}.lib" if os == "windows" else f"lib/lib{name}.a"
                  for name in libraries["tdlib"] + libraries[platform]]
        required = [shared, *static, "include/td/telegram/td_json_client.h",
                    "include/td/telegram/td_file_system.h", "lib/pkgconfig/tdjson.pc"]
        if os == "windows":
            required.append("lib/tdjson.lib")
        for relative in required:
            path = directory / relative
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError(f"invalid native release file (missing or empty): {path}")
    destination.mkdir(parents=True, exist_ok=True)
    for name in sorted(expected):
        directory = source / name
        with zipfile.ZipFile(destination / f"{name}.zip", "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(directory.rglob("*")):
                if path.is_file():
                    archive.write(path, Path("tdlib") / path.relative_to(directory))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    package(args.source, args.destination)
