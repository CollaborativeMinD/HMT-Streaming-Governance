"""Fail if a successful marimo export hides cell failures or oversized output."""

import base64
import hashlib
import json
import re
import sys
from pathlib import Path


def verify(path):
    html = Path(path).read_text()
    start = html.index('"session":') + len('"session":')
    session, _ = json.JSONDecoder().raw_decode(html[start:].lstrip())
    for cell in session["cells"]:
        for output in cell.get("outputs", []):
            if output.get("type") == "error":
                raise ValueError("Notebook cell failed: " + cell["id"])
    if "too large to show" in html or "Your output is too large" in html:
        raise ValueError("Notebook export omitted an oversized output")
    uri = re.search(r"data:application/zip;base64,([A-Za-z0-9+/=]+)", html)
    if uri is None:
        raise ValueError("Source download is missing")
    bundle = Path(__file__).resolve().parents[1] / "engine-and-model.zip"
    if base64.b64decode(uri.group(1)) != bundle.read_bytes():
        raise ValueError("Export source download is stale")
    receipt = dict(
        status="PASS",
        cells=len(session["cells"]),
        cell_errors=0,
        truncated_outputs=0,
        bundle_sha256=hashlib.sha256(bundle.read_bytes()).hexdigest(),
    )
    print(json.dumps(receipt, indent=2))
    return receipt


if __name__ == "__main__":
    verify(sys.argv[1])
