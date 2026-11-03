#!/usr/bin/env python3
"""Verify the complete SDK archive set before release publication."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tarfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from netgen_source import SourceError, expected_source_provenance, patched_source_archive_name


OCCT_RE = re.compile(r"^occt-sdk-([0-9]+\.[0-9]+\.[0-9]+)(?:-r[0-9]+)?$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_info(path: Path) -> dict[str, Any]:
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist() if name == "producer-info.json"]
            if len(names) != 1:
                raise ValueError(f"{path.name}: expected one producer-info.json")
            return json.loads(archive.read(names[0]).decode("utf-8"))
    with tarfile.open(path, "r:gz") as archive:
        members = [member for member in archive.getmembers() if member.isfile() and member.name.endswith("/producer-info.json")]
        if len(members) != 1:
            raise ValueError(f"{path.name}: expected one producer-info.json")
        stream = archive.extractfile(members[0])
        if stream is None:
            raise ValueError(f"{path.name}: cannot read producer-info.json")
        return json.loads(stream.read().decode("utf-8"))


def load_occt_release(tag: str, fixture: Path | None) -> dict[str, Any]:
    if fixture:
        return json.loads(fixture.read_text(encoding="utf-8"))
    token = os.environ.get("GH_TOKEN", "")
    quoted = urllib.parse.quote(tag, safe="")
    request = urllib.request.Request(
        f"https://api.github.com/repos/precise-simulation/OCCT-sdk/releases/tags/{quoted}",
        headers={
            "Authorization": f"Bearer {token}" if token else "",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "thin-netgen-release-verifier",
        },
    )
    with urllib.request.urlopen(request) as response:
        return json.load(response)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def source_tree_entries(source_root: Path) -> dict[str, tuple[str, int | None, str | None]]:
    require(source_root.is_dir(), f"source root does not exist: {source_root}")
    require(not (source_root / "source-provenance.json").exists(), "source root unexpectedly contains source-provenance.json")
    entries: dict[str, tuple[str, int | None, str | None]] = {}
    for path in sorted(source_root.rglob("*"), key=lambda item: item.relative_to(source_root).as_posix()):
        relative = path.relative_to(source_root)
        if relative.parts and relative.parts[0] == ".git":
            continue
        name = relative.as_posix()
        if path.is_symlink():
            entries[name] = ("symlink", None, os.readlink(path))
        elif path.is_dir():
            entries[name] = ("dir", None, None)
        elif path.is_file():
            entries[name] = ("file", stat.S_IMODE(path.stat().st_mode), sha256(path))
        else:
            raise ValueError(f"unsupported source entry type: {path}")
    return entries


def verify_source_archive(source_assets_root: Path, source_root: Path, expected: dict[str, Any]) -> str:
    archive_name = patched_source_archive_name(expected)
    archive_path = source_assets_root / archive_name
    checksum_path = source_assets_root / f"{archive_name}.sha256"
    require(archive_path.is_file(), f"missing source archive: {archive_path}")
    require(checksum_path.is_file(), f"missing source checksum: {checksum_path}")
    checksum_parts = checksum_path.read_text(encoding="utf-8").split()
    require(len(checksum_parts) == 2 and checksum_parts[1] == archive_name, "source checksum sidecar format mismatch")
    require(checksum_parts[0].lower() == sha256(archive_path), "source archive checksum mismatch")

    root_name = archive_name.removesuffix(".tar.gz")
    expected_entries = source_tree_entries(source_root)
    expected_provenance = (json.dumps(expected, indent=2, sort_keys=True) + "\n").encode("utf-8")
    actual_entries: dict[str, tuple[str, int | None, str | None]] = {}
    seen_relative: set[str] = set()
    provenance_seen = False

    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        root_members = [member for member in members if member.name == root_name]
        require(len(root_members) == 1 and root_members[0].isdir(), "source archive root directory mismatch")
        for member in members:
            if member.name == root_name:
                continue
            prefix = f"{root_name}/"
            require(member.name.startswith(prefix), f"source archive member outside root: {member.name}")
            relative = member.name[len(prefix):]
            require(relative and not relative.startswith("/") and ".." not in Path(relative).parts, f"unsafe source archive member: {member.name}")
            require(relative not in seen_relative, f"duplicate source archive member: {relative}")
            seen_relative.add(relative)

            if relative == "source-provenance.json":
                require(member.isfile(), "source-provenance.json is not a regular file")
                stream = archive.extractfile(member)
                require(stream is not None and stream.read() == expected_provenance, "source provenance mismatch")
                provenance_seen = True
                continue

            if member.isfile():
                stream = archive.extractfile(member)
                require(stream is not None, f"cannot read source archive member: {relative}")
                digest = hashlib.sha256(stream.read()).hexdigest()
                actual_entries[relative] = ("file", member.mode & 0o7777, digest)
            elif member.isdir():
                actual_entries[relative.rstrip("/")] = ("dir", None, None)
            elif member.issym():
                actual_entries[relative] = ("symlink", None, member.linkname)
            else:
                raise ValueError(f"unsupported source archive member type: {relative}")

    require(provenance_seen, "source archive is missing source-provenance.json")
    require(actual_entries == expected_entries, "source archive tree differs from reconstructed patched source")
    return archive_name


def verify_occt_asset(info: dict[str, Any], release: dict[str, Any]) -> None:
    occt = info["occt"]
    asset_id = int(occt["asset_id"])
    assets = [asset for asset in release.get("assets", []) if int(asset.get("id", -1)) == asset_id]
    require(len(assets) == 1, f"OCCT asset id {asset_id} is not unique in release")
    asset = assets[0]
    require(asset.get("name") == occt["asset_name"], f"OCCT asset name mismatch for id {asset_id}")
    require(asset.get("browser_download_url") == occt["asset_url"], f"OCCT asset URL mismatch for id {asset_id}")
    require(asset.get("digest") == f"sha256:{occt['sha256']}", f"OCCT asset digest mismatch for id {asset_id}")


def archive_specs(version: str, short_sha: str, occt_version: str) -> list[tuple[str, str, str, bool]]:
    prefix = f"netgen-featool-v{version}-{short_sha}-occt{occt_version}"
    return [
        (f"{prefix}-win64-msvc.zip", "windows", "shared", False),
        (f"{prefix}-win64-msvc-static.zip", "windows", "static", True),
        (f"{prefix}-linux-x86_64-glibc2.17-gcc10.tar.gz", "linux", "shared", False),
        (f"{prefix}-linux-x86_64-glibc2.17-gcc10-static.tar.gz", "linux", "static", False),
        (f"{prefix}-macos13-x86_64-clang.tar.gz", "macos", "shared", False),
        (f"{prefix}-macos13-x86_64-clang-static.tar.gz", "macos", "static", False),
        (f"{prefix}-macos13-arm64-clang.tar.gz", "macos", "shared", False),
        (f"{prefix}-macos13-arm64-clang-static.tar.gz", "macos", "static", False),
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--assets-root", type=Path, required=True)
    parser.add_argument("--source-assets-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--producer-sha", required=True)
    parser.add_argument("--producer-repository", required=True)
    parser.add_argument("--occt-release", required=True)
    parser.add_argument("--occt-release-json", type=Path)
    args = parser.parse_args()

    try:
        expected = expected_source_provenance(args.repo_root.resolve())
        match = OCCT_RE.fullmatch(args.occt_release)
        require(match is not None, f"invalid OCCT release tag: {args.occt_release}")
        occt_version = match.group(1)
        producer_sha = args.producer_sha.lower()
        require(len(producer_sha) == 40 and all(ch in "0123456789abcdef" for ch in producer_sha), "invalid producer SHA")
        short_sha = producer_sha[:12]
        release = load_occt_release(args.occt_release, args.occt_release_json)
        require(release.get("tag_name") == args.occt_release, "OCCT release tag mismatch")
        require(release.get("draft") is False, "OCCT release is draft")
        require(release.get("immutable") is True, "OCCT release is not immutable")

        files = sorted(path for path in args.assets_root.rglob("*") if path.is_file())
        require(len(files) == 16, f"expected 16 release files, found {len(files)}")
        source_files = sorted(path for path in args.source_assets_root.rglob("*") if path.is_file())
        require(len(source_files) == 2, f"expected 2 source release files, found {len(source_files)}")
        source_archive = verify_source_archive(
            args.source_assets_root.resolve(),
            args.source_root.resolve(),
            expected,
        )

        expected_upstream = expected["upstream"]
        expected_patches = expected["patches"]
        occt_source_sha: str | None = None
        for name, directory, linkage, bundled_occt in archive_specs(expected_upstream["version"], short_sha, occt_version):
            archive = args.assets_root / directory / name
            sidecar = archive.with_name(archive.name + ".sha256")
            require(archive.is_file(), f"missing archive: {archive}")
            require(sidecar.is_file(), f"missing checksum: {sidecar}")
            expected_hash = sidecar.read_text(encoding="utf-8").split()[0].lower()
            require(expected_hash == sha256(archive), f"checksum mismatch: {archive.name}")

            info = read_info(archive)
            require(info.get("schema_version") == 2, f"{archive.name}: producer schema is not 2")
            upstream = info.get("upstream", {})
            for key in ("repository", "tag", "commit", "version_git", "version"):
                require(upstream.get(key) == expected_upstream[key], f"{archive.name}: upstream {key} mismatch")
            source = info.get("source", {})
            require(source.get("repository") == args.producer_repository, f"{archive.name}: producer repository mismatch")
            require(str(source.get("commit", "")).lower() == producer_sha, f"{archive.name}: producer commit mismatch")
            require(source.get("patches") == expected_patches, f"{archive.name}: patch provenance mismatch")
            require(isinstance(source.get("ref"), str) and source["ref"], f"{archive.name}: missing producer ref")
            require(info.get("netgen", {}).get("linkage") == linkage, f"{archive.name}: Netgen linkage mismatch")

            occt = info.get("occt", {})
            require(occt.get("release") == args.occt_release, f"{archive.name}: OCCT release mismatch")
            require(occt.get("version") == occt_version, f"{archive.name}: OCCT version mismatch")
            require(occt.get("linkage") == linkage, f"{archive.name}: OCCT linkage mismatch")
            commit = str(occt.get("commit", "")).lower()
            require(len(commit) == 40 and all(ch in "0123456789abcdef" for ch in commit), f"{archive.name}: invalid OCCT source commit")
            if occt_source_sha is None:
                occt_source_sha = commit
            require(commit == occt_source_sha, f"{archive.name}: OCCT source commit differs across archives")
            if bundled_occt:
                require(occt.get("bundled") is True, f"{archive.name}: static Windows OCCT must be bundled")
            verify_occt_asset(info, release)

        print(
            json.dumps(
                {
                    "netgen_tag": expected_upstream["tag"],
                    "netgen_version": expected_upstream["version"],
                    "upstream_commit": expected_upstream["commit"],
                    "occt_source_sha": occt_source_sha,
                    "source_archive": source_archive,
                },
                sort_keys=True,
            )
        )
        return 0
    except (SourceError, OSError, ValueError, KeyError, json.JSONDecodeError, tarfile.TarError, zipfile.BadZipFile) as exc:
        print(f"release verification failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
