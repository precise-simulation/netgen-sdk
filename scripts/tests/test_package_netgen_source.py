#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
from netgen_source import expected_source_provenance, patched_source_archive_name  # noqa: E402


PACKAGE = SCRIPTS / "package-netgen-source.py"


class PackageNetgenSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "tracked.txt").write_bytes(b"patched\n")
        (self.source / "subdir").mkdir()
        (self.source / "subdir" / "data.txt").write_bytes(b"data\n")
        (self.source / ".git" / "objects").mkdir(parents=True)
        (self.source / ".git" / "objects" / "ignored").write_bytes(b"git metadata\n")
        self.provenance = expected_source_provenance(REPO_ROOT)
        self.provenance_path = self.root / "source-provenance.json"
        self.provenance_path.write_text(
            json.dumps(self.provenance, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_package(self, output_dir: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(PACKAGE),
                "--source-root",
                str(self.source),
                "--provenance",
                str(self.provenance_path),
                "--output-dir",
                str(output_dir),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_package_is_deterministic_and_embeds_provenance(self) -> None:
        first = self.root / "first"
        second = self.root / "second"
        first_result = self.run_package(first)
        second_result = self.run_package(second)
        self.assertEqual(first_result.returncode, 0, first_result.stdout + first_result.stderr)
        self.assertEqual(second_result.returncode, 0, second_result.stdout + second_result.stderr)

        name = patched_source_archive_name(self.provenance)
        first_archive = first / name
        second_archive = second / name
        self.assertEqual(first_archive.read_bytes(), second_archive.read_bytes())
        self.assertEqual((first / f"{name}.sha256").read_bytes(), (second / f"{name}.sha256").read_bytes())

        root_name = name.removesuffix(".tar.gz")
        with tarfile.open(first_archive, "r:gz") as archive:
            names = archive.getnames()
            self.assertIn(f"{root_name}/tracked.txt", names)
            self.assertIn(f"{root_name}/subdir/data.txt", names)
            self.assertFalse(any("/.git/" in member or member.endswith("/.git") for member in names))
            provenance_member = archive.extractfile(f"{root_name}/source-provenance.json")
            self.assertIsNotNone(provenance_member)
            assert provenance_member is not None
            self.assertEqual(json.loads(provenance_member.read().decode("utf-8")), self.provenance)

    def test_rejects_provenance_name_collision(self) -> None:
        (self.source / "source-provenance.json").write_text("collision\n", encoding="utf-8")
        result = self.run_package(self.root / "output")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already contains source-provenance.json", result.stderr)


if __name__ == "__main__":
    unittest.main()
