"""Refresh the notebook download after reviewed source/lock changes (run after git add)."""

import ast
import base64
import hashlib
import io
import json
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE = {"governance_lab.py", "governance_lab.html", "engine-and-model.zip"}


def main():
    tracked = (
        subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
        .decode()
        .split("\0")
    )
    files = {
        name: (ROOT / name).read_bytes()
        for name in tracked
        if name and name not in EXCLUDE and (ROOT / name).is_file()
    }
    manifest = {
        name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())
    }
    files["bundle_manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(
        buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    bundle = buffer.getvalue()
    (ROOT / "engine-and-model.zip").write_bytes(bundle)
    path = ROOT / "governance_lab.py"
    source = path.read_text()
    tree = ast.parse(source)
    assignment = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and any(
            isinstance(t, ast.Name) and t.id == "artifact_bundle" for t in n.targets
        )
    )
    node = assignment.value.args[0]
    lines = source.splitlines(keepends=True)
    start = sum(map(len, lines[: node.lineno - 1])) + node.col_offset
    end = sum(map(len, lines[: node.end_lineno - 1])) + node.end_col_offset
    path.write_text(
        source[:start] + repr(base64.b64encode(bundle).decode()) + source[end:]
    )
    print("Bundle SHA256:", hashlib.sha256(bundle).hexdigest(), "files:", len(manifest))


if __name__ == "__main__":
    main()
