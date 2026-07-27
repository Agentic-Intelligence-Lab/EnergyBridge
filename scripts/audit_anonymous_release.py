#!/usr/bin/env python3
"""Fail if an exported review snapshot leaks identity, paths, or results."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import zipfile
from pathlib import Path
from typing import Iterable

SELF = Path(__file__).resolve()
ALLOWED_HUMAN_DATA = {"participants.csv", "responses.csv"}
FORBIDDEN_PREFIXES = (
    "benchmark_results/",
    "paper_results/",
    "generated_results/",
    "reproduced_results/",
    "experiments/benchmark/results/",
    "experiments/benchmark/results_longterm/",
    "experiments/benchmark/memory/",
    "importance_sampling/IS_result/",
    "human_survey_materials/sample_cases/",
    "VPP-1/outputs/",
)
TEXT_PATTERNS = {
    "user home or workspace path": re.compile(
        r"(?<![A-Za-z0-9_])/(?:home|Users|root|workspace|jupyterfile)/"
        r"[A-Za-z0-9_.-]+(?:/|\\b)"
    ),
    "Windows absolute path": re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]"),
    "email address": re.compile(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
    ),
    "private or CGNAT IPv4 address": re.compile(
        r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        r"|192\.168\.\d{1,3}\.\d{1,3}"
        r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
        r"|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\."
        r"\d{1,3}\.\d{1,3})\b"
    ),
    "credential-like token": re.compile(
        r"\b(?:sk-[A-Za-z0-9_-]{12,}"
        r"|gh[pousr]_[A-Za-z0-9]{20,}"
        r"|github_pat_[A-Za-z0-9_]{20,})\b"
    ),
    "private key block": re.compile(r"BEGIN [A-Z ]*PRIVATE KEY"),
    "identifying EnergyBridge repository URL": re.compile(
        r"github\.com/[^/\s]+/EnergyBridge(?:\.git)?",
        re.IGNORECASE,
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "root",
        nargs="?",
        type=Path,
        default=Path.cwd(),
        help="Snapshot root to audit. Defaults to the current directory.",
    )
    parser.add_argument(
        "--forbidden-token",
        action="append",
        default=[],
        help=(
            "Additional case-insensitive identity token. Repeat as needed. "
            "The ANONYMOUS_FORBIDDEN_TOKENS environment variable also accepts "
            "comma-separated tokens."
        ),
    )
    return parser.parse_args()


def iter_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if ".git" in path.relative_to(root).parts:
            continue
        if path.is_file():
            yield path


def is_forbidden_result_path(relative: str) -> bool:
    if relative.startswith(FORBIDDEN_PREFIXES):
        return True
    parts = Path(relative).parts
    return (
        len(parts) >= 3
        and parts[0] == "dr_capacity_memory_toolkit"
        and "data" in parts[2:]
    )


def read_text(path: Path) -> str | None:
    if path.stat().st_size > 10 * 1024 * 1024:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def validate_human_data(root: Path, failures: list[str]) -> None:
    data_dir = root / "human_roleplay_data" / "data"
    if not data_dir.is_dir():
        failures.append("missing human_roleplay_data/data")
        return
    released = {path.name for path in data_dir.iterdir() if path.is_file()}
    if released != ALLOWED_HUMAN_DATA:
        failures.append(
            "human data directory is not source-data-only: "
            + ", ".join(sorted(released))
        )

    participants = data_dir / "participants.csv"
    responses = data_dir / "responses.csv"
    with participants.open(encoding="utf-8", newline="") as handle:
        participant_fields = set(next(csv.DictReader(handle)))
    with responses.open(encoding="utf-8", newline="") as handle:
        response_fields = set(next(csv.DictReader(handle)))
    if participant_fields != {"participant_id", "persona", "age_band"}:
        failures.append("unexpected participant fields")
    if response_fields != {
        "participant_id",
        "persona",
        "method",
        "method_order",
        "acceptance",
        "satisfaction_score",
    }:
        failures.append("unexpected response fields")

    archive = (
        root
        / "human_roleplay_data"
        / "release"
        / "energybridge_human_roleplay_data_anonymous.zip"
    )
    if not archive.is_file():
        failures.append("missing source-data-only questionnaire archive")
        return
    with zipfile.ZipFile(archive) as handle:
        data_members = {
            Path(name).name
            for name in handle.namelist()
            if "/data/" in name and name.endswith(".csv")
        }
        if data_members != ALLOWED_HUMAN_DATA:
            failures.append(
                "questionnaire archive contains derived result tables"
            )


def main() -> None:
    args = parse_args()
    root = args.root.resolve()
    if not root.is_dir():
        raise SystemExit(f"Not a directory: {root}")

    env_tokens = [
        token.strip()
        for token in os.getenv("ANONYMOUS_FORBIDDEN_TOKENS", "").split(",")
        if token.strip()
    ]
    forbidden_tokens = [
        token.casefold()
        for token in [*args.forbidden_token, *env_tokens]
        if token.strip()
    ]
    failures: list[str] = []
    scanned = 0
    for path in iter_files(root):
        relative = path.relative_to(root).as_posix()
        if is_forbidden_result_path(relative):
            failures.append(f"generated or historical result path: {relative}")
            continue
        text = read_text(path)
        if text is None:
            continue
        scanned += 1
        if path.resolve() != SELF:
            for label, pattern in TEXT_PATTERNS.items():
                match = pattern.search(text)
                if match:
                    line = text.count("\n", 0, match.start()) + 1
                    failures.append(f"{label}: {relative}:{line}")
            folded = text.casefold()
            for token in forbidden_tokens:
                if token in folded:
                    failures.append(
                        f"caller-supplied identity token: {relative}"
                    )

    validate_human_data(root, failures)
    manifest_path = root / "human_roleplay_data" / "MANIFEST.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("precomputed_results_included") is not False:
            failures.append(
                "human release manifest does not forbid precomputed results"
            )

    if failures:
        print("Anonymous release audit failed:", file=sys.stderr)
        for failure in sorted(set(failures)):
            print(f"- {failure}", file=sys.stderr)
        raise SystemExit(1)

    print(
        "Anonymous release audit passed: "
        f"{scanned} text files checked; no generated result path found."
    )


if __name__ == "__main__":
    main()
