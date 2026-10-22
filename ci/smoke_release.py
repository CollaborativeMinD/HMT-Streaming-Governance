"""Verify delivered bytes and replay the complete campaign after clean extraction."""

import json
import subprocess
import tempfile
import zipfile
from pathlib import Path

from ci.package_release import PREFIX, ROOT, verify_archive


def main():
    (archive_path,) = (ROOT / "dist").glob("*.zip")
    verify_archive(archive_path)
    with tempfile.TemporaryDirectory(prefix="governance-release-") as folder:
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(folder)
        checkout = Path(folder) / PREFIX
        result = subprocess.run(
            ["uv", "run", "--locked", "--python", "3.12", "run_vv.py"],
            cwd=checkout,
            capture_output=True,
            text=True,
            timeout=240,
        )
        (ROOT / ".ci-output/package-smoke.log").write_text(
            result.stdout + result.stderr
        )
        if result.returncode:
            raise RuntimeError("Extracted campaign failed; see package-smoke.log")
        summary = json.loads((checkout / "vv_evidence/summary.json").read_text())
        if summary["current_results"] != {"PASS": 126} or summary["counts"] != {
            "OUT_OF_SCOPE": 7456,
            "SATISFIED": 32,
        }:
            raise ValueError("Extracted campaign findings differ")
        receipt = dict(
            status="PASS",
            archive=archive_path.name,
            current_results=summary["current_results"],
            counts=summary["counts"],
            source_seal=summary["source_seal"],
        )
        (ROOT / ".ci-output/package-smoke.json").write_text(
            json.dumps(receipt, indent=2) + "\n"
        )
        print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
