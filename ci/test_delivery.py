"""Negative tests for delivery gates; these are additional to the 126 runtime checks."""

import copy
import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from ci.package_release import PREFIX, verify_archive
from ci.verify_evidence import validate


class DeliveryGates(unittest.TestCase):
    def setUp(self):
        self.summary = dict(
            source_seal="current",
            current_results={"PASS": 126},
            counts={"OUT_OF_SCOPE": 7456, "SATISFIED": 32},
            total=7488,
            supersessions=6,
        )
        self.mutations = dict(
            source_seal="current",
            control="PASS",
            results=[dict(id=str(i), outcome="KILLED") for i in range(26)],
        )

    def test_current_complete_campaign_is_accepted(self):
        validate(self.summary, self.mutations, "current")

    def test_stale_source_is_rejected(self):
        with self.assertRaises(ValueError):
            validate(self.summary, self.mutations, "different")

    def test_missing_skipped_or_failed_checks_are_rejected(self):
        for result in (
            {"PASS": 125},
            {"PASS": 125, "SKIP": 1},
            {"PASS": 125, "FAIL": 1},
        ):
            candidate = self.summary | dict(current_results=result)
            with self.assertRaises(ValueError):
                validate(candidate, self.mutations, "current")

    def test_cartesian_failure_cannot_be_hidden(self):
        with self.assertRaises(ValueError):
            validate(
                self.summary
                | dict(counts={"OUT_OF_SCOPE": 7456, "SATISFIED": 31, "FAILED": 1}),
                self.mutations,
                "current",
            )

    def test_surviving_or_invalid_mutant_is_rejected(self):
        for state in ("SURVIVED", "INVALID"):
            candidate = copy.deepcopy(self.mutations)
            candidate["results"][0]["outcome"] = state
            with self.assertRaises(ValueError):
                validate(self.summary, candidate, "current")

    def test_duplicate_mutants_are_not_independent_kills(self):
        candidate = copy.deepcopy(self.mutations)
        candidate["results"][1]["id"] = "0"
        with self.assertRaises(ValueError):
            validate(self.summary, candidate, "current")

    def test_changed_and_unlisted_archive_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "release.zip"
            manifest = {"source.py": hashlib.sha256(b"expected").hexdigest()}
            for value, extra in (
                (b"expected", False),
                (b"changed", False),
                (b"expected", True),
            ):
                with zipfile.ZipFile(path, "w") as archive:
                    archive.writestr(PREFIX + "MANIFEST.json", json.dumps(manifest))
                    archive.writestr(PREFIX + "source.py", value)
                    if extra:
                        archive.writestr(PREFIX + "extra.py", b"extra")
                if value == b"expected" and not extra:
                    verify_archive(path)
                else:
                    with self.assertRaises(ValueError):
                        verify_archive(path)
