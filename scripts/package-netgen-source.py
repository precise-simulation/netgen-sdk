#!/usr/bin/env python3
"""Create a deterministic archive of the reconstructed patched Netgen source."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import stat
import sys
import tarfile
from pathlib import Path

from netgen_source import SourceError, patched_source_archive_name


def iter_source_entries(source_root: Path):
    def walk(directory: Path, relative: Path):
        with os.scandir(directory) as scan:
            entries = sorted(scan, key=lambda entry: entry.name)
        for entry in entries:
            if not relative.parts and entry.name == ".git":
                continue
            path = Path(entry.path)
            rel = relative / entry.name
            if entry.is_symlink():
                yield path, rel
            elif entry.is_dir(follow_symlinks=False):
                yield path, rel
                yield from walk(path, rel)
            elif entry.is_file(follow_symlinks=False):
                yield path, rel
            else:
                raise SourceError(f"unsupported source entry type: {path}")

    yield from walk(source_root, Path())


def normalize_tar_info(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.pax_headers = {}
    return info


def package(source_root: Path, provenance_path: Path, output_dir: Path) -> tuple[Path, Path]:
    if not source_root.is_dir():
        raise SourceError(f"source root does not exist: {source_root}")
    if (source_root / "source-provenance.json").exists():
        raise SourceError("source tree already contains source-provenance.json")
    try:
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceError(f"invalid source provenance: {provenance_path}") from exc

    canonical_provenance = (json.dumps(provenance, indent=2, sort_keys=True) + "\n").encode("utf-8")
    archive_name = patched_source_archive_name(provenance)
    root_name = archive_name.removesuffix(".tar.gz")
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir / archive_name
    checksum_path = output_dir / f"{archive_name}.sha256"
    if archive_path.exists() or checksum_path.exists():
        raise SourceError(f"source release output already exists in {output_dir}")

    with archive_path.open("wb") as raw_stream:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_stream, compresslevel=9, mtime=0) as gzip_stream:
            with tarfile.open(fileobj=gzip_stream, mode="w", format=tarfile.PAX_FORMAT) as archive:
                root_info = tarfile.TarInfo(root_name)
                root_info.type = tarfile.DIRTYPE
                root_info.mode = 0o755
                archive.addfile(normalize_tar_info(root_info))

                for path, relative in iter_source_entries(source_root):
                    arcname = f"{root_name}/{relative.as_posix()}"
                    info = normalize_tar_info(archive.gettarinfo(str(path), arcname=arcname))
                    if info.isfile():
                        info.mode = stat.S_IMODE(path.stat().st_mode)
                        with path.open("rb") as stream:
                            archive.addfile(info, stream)
                    else:
                        archive.addfile(info)

                provenance_info = tarfile.TarInfo(f"{root_name}/source-provenance.json")
                provenance_info.size = len(canonical_provenance)
                provenance_info.mode = 0o644
                archive.addfile(normalize_tar_info(provenance_info), fileobj=io.BytesIO(canonical_provenance))

    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    checksum_path.write_text(f"{digest}  {archive_name}\n", encoding="utf-8", newline="\n")
    return archive_path, checksum_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    try:
        archive, checksum = package(
            args.source_root.resolve(),
            args.provenance.resolve(),
            args.output_dir.resolve(),
        )
    except SourceError as exc:
        print(f"package-netgen-source: {exc}", file=sys.stderr)
        return 1

    print(archive)
    print(checksum)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
