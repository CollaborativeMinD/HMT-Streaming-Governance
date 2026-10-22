# Qualification and delivery runbook

The active lane is [`.github/workflows/ci.yml`](../.github/workflows/ci.yml). It adapts the evidence-bound admission and tag-only delivery patterns used in HMT Career Search, HMT Debugger Agent and HMT Mission Assurance. It does not import or claim execution of their private CI platform, signed-grant, container, browser, robotic or production-deployment toolchains.

| Lane | Required result | Scope |
|---|---|---|
| V&V, Python 3.11/3.12/3.13 | All 126 executions, 26 semantic kills, 32 satisfied contracts, 7,456 retained scope rows, 6 failure supersessions | Full finite-catalog campaign on each interpreter |
| Model/notebook/supply chain | Strict SysML validation, actual notebook export, pandas load, vulnerability and secret scans, workflow syntax | Pinned tools; current tree plus two archive levels; published vulnerability data at run time |
| Admission | Every upstream lane succeeds | Failure, cancellation and skip prevent packaging |
| Verified archive/clean replay | Per-file manifest, archive checksum, complete campaign after clean extraction | Exact source and fresh Python 3.12/tooling evidence |
| Tag delivery | Same verified ZIP/checksum uploaded to GitHub Release | No service activation or agent-host registration |

The runtime uses the standard library. The MCP adapter and V&V runner use their separate PEP 723 locks. SysML tooling uses `package-lock.json`; Node.js 22 and uv 0.12.23 are explicit. Actions are pinned to immutable commits. `pip-audit` is pinned to 2.10.1; vulnerability data remains time dependent. Gitleaks 8.30.1 and actionlint 1.7.12 downloads are SHA-256 verified.

The secret-scan exceptions are restricted to the generic-key rule: receipt UUIDs grant no authentication authority, and source-hash dictionary keys containing `api` are not credentials. Provider-specific detectors remain enabled. Dependencies, Git metadata and generated CI output are excluded from current-tree credential scanning. This is not a claim that every possible encoded secret is detectable.

## Inspect a PR

1. Open **Actions → Qualification and delivery** for the PR's current head.
2. Require all three V&V matrix jobs, tooling, Admission and Verified archive/clean replay to succeed.
3. Download `qualification-python-*`, `tooling-evidence` and `verified-delivery`. Failure artifacts are preserved even when a job fails; their existence is not admission.
4. Compare the source seal, XML/JSON results, retained mutation logs, SQL finding dispositions and package smoke receipt. The release manifest binds every delivered source/evidence file to its SHA-256.
5. Merge only the reviewed, passing head. A successor needs another run.

Repository branch protection is separate from workflow logic. Recommended required checks are **Admission** and **Verified archive and clean replay**, with PRs required. A workflow cannot prevent an administrator or unprotected direct push; the workflow token does not gain repository-administration rights.

## Reproduce locally

```bash
uv run --locked --python 3.12 run_vv.py
python ci/verify_evidence.py
python -m unittest ci.test_delivery -v
mkdir -p .ci-output
npm ci --ignore-scripts
npm run check:model
uvx --python 3.13 marimo@0.25.1 check governance_lab.py
```

After a reviewed source/lock/model update, stage the intended input files, then refresh the notebook download:

```bash
git add .
python -m ci.refresh_notebook_bundle
uvx --python 3.13 marimo@0.25.1 export html governance_lab.py --no-include-code -o governance_lab.html --force
git add engine-and-model.zip governance_lab.py governance_lab.html
```

The notebook embeds an inspectable snapshot, not a live import of repository changes. CI rejects a stale runtime/model/lock snapshot. New source must retain or strengthen the tests and update the declared profile deliberately; do not shrink requirements or relabel failures to obtain green.

Package from a committed checkout after successful qualification:

```bash
python -m ci.package_release
python -m ci.smoke_release
```

`dist/` is ignored. The ZIP includes source, documentation, locks, fresh SQL/CSV/results, per-file `MANIFEST.json`, and a `release.json` with the source commit and workflow run. Packaging checks and the clean replay are additional to the 126 runtime/refutation executions. Source hashes establish consistency, not independent authenticity or certification.

## Deliver a version

After the reviewed main commit passes, create a version tag on that exact commit:

```bash
git switch main
git pull --ff-only
git tag v0.1.0
git push origin v0.1.0
```

The tag starts the complete lane again. Release write permission exists only in the final job; it consumes the already smoke-tested artifact and verifies its checksum. A blocked tag creates no release. The workflow never replaces an existing release asset silently; create a new reviewed version for a change. Actions artifacts retain run evidence for 30 days; GitHub Release assets provide versioned delivery beyond that window.

Visibility stays as configured by the owner. This setup does not make a private repository public. Release assets inherit repository visibility; changing visibility and choosing a project license are owner decisions.

## Durable successor lane

The `durable` job now participates in Admission alongside the three-version reference campaign and tooling job. It builds the pinned Go bridge, runs native Go checks and an explicit govulncheck JSON call-graph gate, creates a disposable TLS PostgreSQL/Redis/OpenBao/Headscale lab, executes the full durable pytest/unittest/mutation/SQL/model campaign, audits locked Python dependencies and checks publication bytes against its actual lab-secret inventory. Cleanup runs even on failure; credentials are never uploaded.

Packaging restores the durable lane's current evidence, verifies its source manifest, and replays both profiles from a manifest-verified extraction. Deployment HOLDs remain explicit in the durable report and do not become runtime passes. Native tests fail if the bridge or lab is missing; there is no qualification skip path. The Forgejo-compatible entrypoint is `service/run_vv.py`; the deployment runbook explains runner and secret boundaries.
