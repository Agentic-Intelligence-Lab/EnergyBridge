#!/usr/bin/env python3
"""Validate privacy, integrity, pairing, and paper-facing study invariants."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

RELEASE_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = RELEASE_ROOT / "data"
METHODS = {"MPC", "HEMA", "EnergyBridge"}
FORBIDDEN_PARTICIPANT_FIELDS = {
    "age",
    "city",
    "province",
    "gender",
    "ip",
    "email",
    "name",
    "submission_time",
    "duration",
}
FORBIDDEN_TEXT_PATTERNS = {
    "Unix home path": re.compile(r"/home/[A-Za-z0-9_.-]+/"),
    "macOS home path": re.compile(r"/Users/[A-Za-z0-9_.-]+/"),
    "Windows drive path": re.compile(r"\b[A-Za-z]:\\"),
    "email address": re.compile(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
    ),
    "API key prefix": re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    "private IPv4 address": re.compile(
        r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        r"|192\.168\.\d{1,3}\.\d{1,3}"
        r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"
    ),
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fail(message: str) -> None:
    raise AssertionError(message)


def validate_microdata() -> None:
    participants_path = DATA_DIR / "participants.csv"
    responses_path = DATA_DIR / "responses.csv"
    participants = read_csv(participants_path)
    responses = read_csv(responses_path)

    if len(participants) != 584:
        fail(f"Expected 584 participants, found {len(participants)}")
    if len(responses) != 1_752:
        fail(f"Expected 1,752 responses, found {len(responses)}")

    participant_fields = set(participants[0])
    forbidden = participant_fields & FORBIDDEN_PARTICIPANT_FIELDS
    if forbidden:
        fail(f"Forbidden participant-level fields: {sorted(forbidden)}")
    if participant_fields != {"participant_id", "persona", "age_band"}:
        fail(f"Unexpected participant fields: {sorted(participant_fields)}")

    participant_ids = [row["participant_id"] for row in participants]
    if len(set(participant_ids)) != len(participant_ids):
        fail("Duplicate participant IDs")
    if not all(re.fullmatch(r"R\d{4}", value) for value in participant_ids):
        fail("Release IDs do not use the approved random-ID format")

    participant_by_id = {row["participant_id"]: row for row in participants}
    by_participant: dict[str, list[dict[str, str]]] = defaultdict(list)
    method_counts: Counter[str] = Counter()
    for row in responses:
        participant_id = row["participant_id"]
        if participant_id not in participant_by_id:
            fail(f"Response references unknown participant: {participant_id}")
        if row["persona"] != participant_by_id[participant_id]["persona"]:
            fail(f"Persona mismatch for {participant_id}")
        if row["method"] not in METHODS:
            fail(f"Unexpected method: {row['method']}")
        if row["acceptance"] not in {"0", "1"}:
            fail(f"Non-binary acceptance for {participant_id}")
        score = float(row["satisfaction_score"])
        if not 0.0 <= score <= 5.0:
            fail(f"Satisfaction outside [0,5] for {participant_id}")
        by_participant[participant_id].append(row)
        method_counts[row["method"]] += 1

    if set(by_participant) != set(participant_ids):
        fail("Participant and response ID sets differ")
    for participant_id, rows in by_participant.items():
        methods = {row["method"] for row in rows}
        if len(rows) != 3 or methods != METHODS:
            fail(f"Incomplete method block for {participant_id}: {methods}")
    if method_counts != Counter({method: 584 for method in METHODS}):
        fail(f"Method counts differ: {dict(method_counts)}")

    released_cells = Counter(
        (row["persona"], row["age_band"]) for row in participants
    )
    if min(released_cells.values()) < 5:
        fail(f"Released demographic cell below five: {released_cells}")


def validate_checksums() -> None:
    checksum_path = RELEASE_ROOT / "SHA256SUMS.txt"
    for line in checksum_path.read_text(encoding="utf-8").splitlines():
        expected, relative = line.split("  ", 1)
        path = RELEASE_ROOT / relative
        if not path.is_file():
            fail(f"Checksum target is missing: {relative}")
        actual = sha256(path)
        if actual != expected:
            fail(f"Checksum mismatch for {relative}: {actual} != {expected}")


def validate_text_privacy() -> None:
    for path in RELEASE_ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() == ".zip":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for label, pattern in FORBIDDEN_TEXT_PATTERNS.items():
            match = pattern.search(text)
            if match:
                fail(f"{label} found in {path.relative_to(RELEASE_ROOT)}")


def validate_manifest() -> None:
    manifest = json.loads((RELEASE_ROOT / "MANIFEST.json").read_text())
    if manifest["participants"] != 584 or manifest["judgments"] != 1_752:
        fail("Manifest record counts differ")
    if manifest.get("precomputed_results_included") is not False:
        fail("Manifest must declare a source-data-only release")
    forbidden = set(manifest["participant_fields"]) & FORBIDDEN_PARTICIPANT_FIELDS
    if forbidden:
        fail(f"Manifest advertises forbidden fields: {sorted(forbidden)}")


def validate_archive() -> None:
    path = RELEASE_ROOT / "release" / "energybridge_human_roleplay_data_anonymous.zip"
    if not path.is_file():
        fail(f"Missing release archive: {path}")
    with zipfile.ZipFile(path) as handle:
        bad = handle.testzip()
        if bad is not None:
            fail(f"Corrupt archive member: {bad}")
        names = handle.namelist()
        if not names or not all(name.startswith("human_roleplay_data/") for name in names):
            fail("Unexpected archive root")
        allowed_data = {"participants.csv", "responses.csv"}
        released_data = {
            Path(name).name
            for name in names
            if "/data/" in name and name.endswith(".csv")
        }
        if released_data != allowed_data:
            fail(
                "Archive data files differ from source-data-only policy: "
                f"{sorted(released_data)}"
            )


def main() -> None:
    validate_microdata()
    validate_manifest()
    validate_checksums()
    validate_text_privacy()
    validate_archive()
    print("Human role-play release validation passed.")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
