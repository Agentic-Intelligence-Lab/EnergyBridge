#!/usr/bin/env python3
"""Build the source-data-only public release from the private transfer ZIP.

The input archive is intentionally not part of the repository. This script
rekeys participant IDs without retaining the source-to-release mapping and
removes participant-level geography, gender, exact age, and every precomputed
analysis result.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import secrets
import zipfile
from pathlib import Path
from typing import Iterable

RELEASE_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = RELEASE_ROOT / "data"
ARCHIVE_DIR = RELEASE_ROOT / "release"
ARCHIVE_NAME = "energybridge_human_roleplay_data_anonymous.zip"

METHODS = ("MPC", "HEMA", "EnergyBridge")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source_zip",
        type=Path,
        help="Private transfer ZIP. It is read but never copied into the repository.",
    )
    return parser.parse_args()


def _member_map(handle: zipfile.ZipFile) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in handle.namelist():
        if name.endswith("/"):
            continue
        result[name.rsplit("/", 1)[-1]] = name
    return result


def _read_csv(
    handle: zipfile.ZipFile,
    members: dict[str, str],
    filename: str,
) -> list[dict[str, str]]:
    member = members.get(filename)
    if member is None:
        raise ValueError(f"Missing {filename} in source ZIP")
    text = handle.read(member).decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def _write_csv(
    path: Path,
    rows: Iterable[dict[str, object]],
    fieldnames: list[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _age_band(raw_age: str) -> str:
    age = int(raw_age)
    if 20 <= age <= 24:
        return "20-24"
    if 25 <= age <= 34:
        return "25-34"
    if 35 <= age <= 44:
        return "35-44"
    if 45 <= age <= 55:
        return "45-55"
    raise ValueError(f"Age outside approved release bands: {age}")


def _random_id_map(source_ids: list[str]) -> dict[str, str]:
    shuffled = list(source_ids)
    secrets.SystemRandom().shuffle(shuffled)
    return {
        source_id: f"R{index:04d}"
        for index, source_id in enumerate(shuffled, start=1)
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _release_files() -> list[Path]:
    excluded = {"SHA256SUMS.txt", ARCHIVE_NAME}
    return sorted(
        path
        for path in RELEASE_ROOT.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.name not in excluded
        and "release" not in path.relative_to(RELEASE_ROOT).parts
    )


def _write_checksums() -> None:
    lines = [
        f"{_sha256(path)}  {path.relative_to(RELEASE_ROOT).as_posix()}"
        for path in _release_files()
    ]
    (RELEASE_ROOT / "SHA256SUMS.txt").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def _write_deterministic_archive() -> None:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archive_path = ARCHIVE_DIR / ARCHIVE_NAME
    files = _release_files() + [RELEASE_ROOT / "SHA256SUMS.txt"]
    with zipfile.ZipFile(
        archive_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as handle:
        for path in sorted(files):
            relative = path.relative_to(RELEASE_ROOT).as_posix()
            info = zipfile.ZipInfo(f"human_roleplay_data/{relative}")
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            handle.writestr(info, path.read_bytes())


def main() -> None:
    args = parse_args()
    source_zip = args.source_zip.resolve()
    if not source_zip.is_file():
        raise FileNotFoundError(source_zip)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for existing in DATA_DIR.iterdir():
        if existing.is_file() and existing.name not in {
            "participants.csv",
            "responses.csv",
        }:
            existing.unlink()
    with zipfile.ZipFile(source_zip) as source:
        members = _member_map(source)
        participants = _read_csv(source, members, "participants.csv")
        responses = _read_csv(source, members, "responses.csv")

        source_ids = [row["participant_id"] for row in participants]
        if len(source_ids) != 584 or len(set(source_ids)) != 584:
            raise ValueError("Expected 584 unique source participant IDs")
        release_ids = _random_id_map(source_ids)

        public_participants = [
            {
                "participant_id": release_ids[row["participant_id"]],
                "persona": row["persona"],
                "age_band": _age_band(row["age"]),
            }
            for row in participants
        ]
        public_participants.sort(key=lambda row: row["participant_id"])
        _write_csv(
            DATA_DIR / "participants.csv",
            public_participants,
            ["participant_id", "persona", "age_band"],
        )

        public_responses = [
            {
                "participant_id": release_ids[row["participant_id"]],
                "persona": row["persona"],
                "method": row["method"],
                "method_order": row["method_order"],
                "acceptance": row["acceptance"],
                "satisfaction_score": row["satisfaction_score"],
            }
            for row in responses
        ]
        public_responses.sort(
            key=lambda row: (
                row["participant_id"],
                int(row["method_order"]),
            )
        )
        _write_csv(
            DATA_DIR / "responses.csv",
            public_responses,
            [
                "participant_id",
                "persona",
                "method",
                "method_order",
                "acceptance",
                "satisfaction_score",
            ],
        )

    method_counts = {
        method: sum(row["method"] == method for row in public_responses)
        for method in METHODS
    }
    persona_counts: dict[str, int] = {}
    for row in public_participants:
        persona = str(row["persona"])
        persona_counts[persona] = persona_counts.get(persona, 0) + 1
    manifest = {
        "release": "EnergyBridge human role-play authorization data",
        "language": "English",
        "encoding": "UTF-8",
        "participants": len(public_participants),
        "judgments": len(public_responses),
        "methods_per_participant": 3,
        "participant_fields": ["participant_id", "persona", "age_band"],
        "response_fields": [
            "participant_id",
            "persona",
            "method",
            "method_order",
            "acceptance",
            "satisfaction_score",
        ],
        "persona_counts": persona_counts,
        "method_counts": method_counts,
        "included_data_files": [
            "data/participants.csv",
            "data/responses.csv",
        ],
        "precomputed_results_included": False,
        "privacy": {
            "release_ids": (
                "New random IDs; the source-to-release mapping was not written."
            ),
            "participant_level_removed": [
                "city",
                "province",
                "gender",
                "exact_age",
                "IP_address",
                "submission_time",
                "response_duration",
                "free_text",
                "source_metadata",
            ],
            "geography": "Not released.",
            "free_text_released": False,
        },
        "analysis_policy": (
            "No precomputed tables are distributed. Run reproduce_analysis.py "
            "to create outputs outside this directory."
        ),
    }
    (RELEASE_ROOT / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _write_checksums()
    _write_deterministic_archive()

    print(f"Built public release in {RELEASE_ROOT}")
    print(f"Archive: {ARCHIVE_DIR / ARCHIVE_NAME}")


if __name__ == "__main__":
    main()
