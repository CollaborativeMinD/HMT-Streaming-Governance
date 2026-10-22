"""Reject failed/stale-source campaign evidence; check the notebook's source download."""

import ast
import base64
import hashlib
import io
import json
import sqlite3
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def notebook_bundle(root=ROOT):
    tree = ast.parse((root / "governance_lab.py").read_text())
    node = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and any(
            isinstance(t, ast.Name) and t.id == "artifact_bundle" for t in n.targets
        )
    )
    data = base64.b64decode(ast.literal_eval(node.value.args[0]), validate=True)
    if data != (root / "engine-and-model.zip").read_bytes():
        raise ValueError("Notebook download differs from delivered bundle")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        manifest = json.loads(archive.read("bundle_manifest.json"))
        if set(archive.namelist()) != set(manifest) | {"bundle_manifest.json"}:
            raise ValueError("Bundle manifest is incomplete")
        for name, expected in manifest.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                raise ValueError("Bundle checksum mismatch: " + name)
        for path in [
            *root.glob("governance_*.py"),
            root / "governance.sysml",
            root / "governance_mcp.py.lock",
            root / "run_vv.py.lock",
        ]:
            if path.name == "governance_lab.py":
                continue
            if archive.read(path.name) != path.read_bytes():
                raise ValueError("Notebook source snapshot is stale: " + path.name)
    return hashlib.sha256(data).hexdigest()


def validate(summary, mutations, expected_seal):
    if (
        summary["source_seal"] != expected_seal
        or mutations["source_seal"] != expected_seal
    ):
        raise ValueError("Stale source evidence")
    if summary["current_results"] != {"PASS": 126}:
        raise ValueError("Campaign has missing, failed or skipped checks")
    if (
        summary["counts"] != {"OUT_OF_SCOPE": 7456, "SATISFIED": 32}
        or summary["total"] != 7488
    ):
        raise ValueError("Unresolved or unexpected Cartesian findings")
    if summary["supersessions"] != 6:
        raise ValueError("Historical failure links are missing")
    rows = mutations["results"]
    if (
        mutations["control"] != "PASS"
        or len(rows) != 26
        or len({r["id"] for r in rows}) != 26
    ):
        raise ValueError("Mutation positive control/count is invalid")
    if any(row["outcome"] != "KILLED" for row in rows):
        raise ValueError("Mutation survived or was invalid")


def main():
    import sys

    sys.path.insert(0, str(ROOT))
    from vv_common import source_seal

    summary = json.loads((ROOT / "vv_evidence/summary.json").read_text())
    mutations = json.loads((ROOT / "vv_evidence/mutation_results.json").read_text())
    validate(summary, mutations, source_seal())
    with sqlite3.connect(":memory:") as db:
        db.executescript((ROOT / "vv_evidence/assurance.sql").read_text())
        db.execute("PRAGMA foreign_keys=ON")
        if (
            db.execute("PRAGMA foreign_key_check").fetchall()
            or db.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
        ):
            raise ValueError("SQL dump is not restorable")
        if db.execute("SELECT COUNT(*) FROM finding_evidence").fetchone()[0] != 14976:
            raise ValueError("Missing finding-to-assertion evidence")
    receipt = dict(
        status="PASS",
        source_seal=source_seal(),
        current_checks=126,
        killed_mutants=26,
        combinations=7488,
        behavioral_obligations=32,
        out_of_scope=7456,
        historical_supersessions=6,
        notebook_bundle_sha256=notebook_bundle(),
        sql_dump_replay="PASS",
    )
    output = ROOT / ".ci-output"
    output.mkdir(exist_ok=True)
    (output / "evidence-verification.json").write_text(
        json.dumps(receipt, indent=2) + "\n"
    )
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
