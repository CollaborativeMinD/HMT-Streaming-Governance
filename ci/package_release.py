"""Build and verify a source/evidence ZIP from tracked files and fresh CI outputs."""

import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "hmt-streaming-governance/"


def verify_archive(path):
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError("ZIP integrity failure")
        manifest = json.loads(archive.read(PREFIX + "MANIFEST.json"))
        if set(archive.namelist()) != {PREFIX + n for n in manifest} | {
            PREFIX + "MANIFEST.json"
        }:
            raise ValueError("Unlisted or missing artifact")
        for name, expected in manifest.items():
            candidate = Path(name)
            if candidate.is_absolute() or ".." in candidate.parts:
                raise ValueError("Unsafe archive path")
            if hashlib.sha256(archive.read(PREFIX + name)).hexdigest() != expected:
                raise ValueError("Artifact checksum mismatch: " + name)
    return manifest


def build(root=ROOT):
    import sys

    sys.path.insert(0, str(root))
    from ci.verify_evidence import main

    main()
    if (root / "service/evidence/summary.json").exists():
        subprocess.run(["uv","run","--locked","--project","service","python","-m","vv.verify"],
                       cwd=root,env=__import__('os').environ | {"PYTHONPATH":str(root/"service")},check=True)
    tracked = (
        subprocess.check_output(["git", "ls-files", "-z"], cwd=root)
        .decode()
        .split("\0")
    )
    files = {
        name: (root / name).read_bytes()
        for name in tracked
        if name and (root / name).is_file()
    }
    # A restored qualification artifact contains the fresh run-specific evidence.
    for directory in ("vv_evidence", ".ci-output"):
        for path in (root / directory).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                files[path.relative_to(root).as_posix()] = path.read_bytes()
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    import os

    files["release.json"] = (
        json.dumps(
            dict(
                source_commit=commit,
                repository=os.environ.get(
                    "GITHUB_REPOSITORY", "CollaborativeMinD/HMT-Streaming-Governance"
                ),
                workflow_run=os.environ.get("GITHUB_RUN_ID"),
                profile="in-memory reference plus durable PostgreSQL/Tailcat local qualification; target deployment HOLDs retained",
                activation="No service activation; no agent-host registration",
            ),
            indent=2,
        )
        + "\n"
    ).encode()
    manifest = {
        name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())
    }
    files["MANIFEST.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    output = root / "dist"
    output.mkdir(exist_ok=True)
    archive_path = output / ("hmt-streaming-governance-" + commit[:12] + ".zip")
    with zipfile.ZipFile(
        archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(PREFIX + name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    verify_archive(archive_path)
    archive_path.with_suffix(".zip.sha256").write_text(
        hashlib.sha256(archive_path.read_bytes()).hexdigest()
        + "  "
        + archive_path.name
        + "\n"
    )
    return archive_path


if __name__ == "__main__":
    print(build())
