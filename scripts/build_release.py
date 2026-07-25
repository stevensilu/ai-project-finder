#!/usr/bin/env python3
"""Build language- and platform-specific release archives from one Git commit."""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EDITIONS = {
    ("EN", "macOS"): "en",
    ("EN", "Windows"): "en",
    ("ZH", "macOS"): "zh-CN",
    ("ZH", "Windows"): "zh-CN",
}


def git_output(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args],
        cwd=ROOT,
        text=True,
    ).strip()


def export_commit(commit: str, destination: Path) -> None:
    archive = subprocess.check_output(
        ["git", "archive", "--format=tar", commit],
        cwd=ROOT,
    )
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
        bundle.extractall(destination, filter="data")


def configure_edition(root: Path, locale: str, platform: str) -> None:
    config_path = root / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["locale"] = locale
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if locale == "zh-CN":
        (root / "README.md").write_text(
            (root / "README.zh-CN.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (root / "README.zh-CN.md").unlink()
    else:
        (root / "README.zh-CN.md").unlink()

    if platform == "macOS":
        for name in ("install.bat", "start.bat"):
            (root / name).unlink()
    else:
        for name in ("install.command", "start.command"):
            (root / name).unlink()


def normalize_mtime(root: Path, epoch: int) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        os.utime(path, (epoch, epoch), follow_symlinks=False)
    os.utime(root, (epoch, epoch), follow_symlinks=False)


def write_zip(source: Path, output: Path, epoch: int) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()

    root_name = "AI_Project_Finder"
    stamp = time.gmtime(max(epoch, 315532800))[:6]
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        directory_info = zipfile.ZipInfo(f"{root_name}/", date_time=stamp)
        directory_info.external_attr = (0o40755 << 16) | 0x10
        archive.writestr(directory_info, b"")

        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source).as_posix()
            arcname = f"{root_name}/{relative}"
            info = zipfile.ZipInfo(
                arcname + ("/" if path.is_dir() else ""),
                date_time=stamp,
            )
            mode = path.stat().st_mode
            info.external_attr = mode << 16
            if path.is_dir():
                info.external_attr |= 0x10
                archive.writestr(info, b"")
            else:
                archive.writestr(
                    info,
                    path.read_bytes(),
                    compress_type=zipfile.ZIP_DEFLATED,
                    compresslevel=9,
                )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True, help="Release version, for example v1.2.0")
    parser.add_argument("--commit", default="HEAD", help="Git commit to package")
    parser.add_argument("--output-dir", default=str(ROOT / "outputs"))
    args = parser.parse_args()

    commit = git_output("rev-parse", args.commit)
    epoch = int(git_output("show", "-s", "--format=%ct", commit))
    output_dir = Path(args.output_dir).expanduser().resolve()

    built: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="ai-project-finder-release-") as temp:
        temp_root = Path(temp)
        pristine = temp_root / "pristine"
        pristine.mkdir()
        export_commit(commit, pristine)

        for (language, platform), locale in EDITIONS.items():
            edition = temp_root / f"{language}-{platform}"
            shutil.copytree(pristine, edition)
            configure_edition(edition, locale, platform)
            normalize_mtime(edition, epoch)

            archive_name = (
                f"AI_Project_Finder_{language}_{platform}_{args.version}.zip"
            )
            output = output_dir / archive_name
            write_zip(edition, output, epoch)
            built.append(output)

    print(f"Packaged commit {commit}")
    for path in built:
        print(path)


if __name__ == "__main__":
    main()
