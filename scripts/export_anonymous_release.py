#!/usr/bin/env python3
"""Export a tracked-only, result-free snapshot with optional fresh Git history."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AUDITOR = REPO_ROOT / "scripts" / "audit_anonymous_release.py"
EXCLUDED_PREFIXES = (
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
    "dr_capacity_memory_toolkit/june_2025_daily_eb_rule_milp/",
)
EXCLUDED_EXACT = {
    "REFERENCE_CAPACITY_RL_INTEGRATION.md",
    "Family_Model/envelope_retrofit_report.md",
    "Family_Model/envelope_retrofit_report_draft_en.md",
    "baselines/rl_energyplus/README.md",
    "docs/DEV_NOTES.md",
    "experiments/benchmark/baselines/mpc/dynamic_model/assets/complete_sinergym_long/metrics/complete_sinergym_long_metrics.json",
    "experiments/benchmark/baselines/mpc/dynamic_model/assets/regional_5r3c/berlin/metrics_5r3c_hvac_solar.json",
    "experiments/benchmark/baselines/mpc/dynamic_model/assets/regional_5r3c/berlin/power_model_metrics.json",
    "experiments/benchmark/baselines/mpc/dynamic_model/assets/thermal_improvement_experiments/summary.json",
    "tests/test_total_quantification.py",
    "experiments/benchmark/reproduce_benchmark.sh",
    "experiments/models/medium_office/README.md",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="New, non-existing directory for the snapshot.",
    )
    parser.add_argument(
        "--archive",
        type=Path,
        help="Optional deterministic ZIP path. Must not already exist.",
    )
    parser.add_argument(
        "--init-git",
        action="store_true",
        help="Initialize a new one-commit anonymous Git history.",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Allow tracked worktree changes. Intended only for local testing.",
    )
    parser.add_argument(
        "--forbidden-token",
        action="append",
        default=[],
        help="Additional identity token forwarded to the audit.",
    )
    return parser.parse_args()


def run(
    command: list[str],
    *,
    cwd: Path = REPO_ROOT,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def tracked_files() -> list[str]:
    output = subprocess.check_output(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
    )
    return sorted(
        item.decode("utf-8")
        for item in output.split(b"\0")
        if item
    )


def excluded(relative: str) -> bool:
    if relative in EXCLUDED_EXACT or relative.startswith(EXCLUDED_PREFIXES):
        return True
    parts = Path(relative).parts
    return (
        len(parts) >= 3
        and parts[0] == "dr_capacity_memory_toolkit"
        and "data" in parts[2:]
    )


def copy_snapshot(output_dir: Path) -> tuple[int, int]:
    included = 0
    omitted = 0
    for relative in tracked_files():
        if excluded(relative):
            omitted += 1
            continue
        source = (REPO_ROOT / relative).resolve()
        try:
            source.relative_to(REPO_ROOT)
        except ValueError as exc:
            raise RuntimeError(f"Tracked path escapes repository: {relative}") from exc
        if not source.is_file():
            raise RuntimeError(f"Tracked file is missing: {relative}")
        target = output_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        included += 1
    return included, omitted


def write_manifest(output_dir: Path, included: int, omitted: int) -> None:
    manifest = {
        "release_profile": "anonymous-source-data-and-benchmark-code",
        "history_inherited": False,
        "historical_experiment_results_included": False,
        "tracked_files_included": included,
        "tracked_files_omitted_by_policy": omitted,
        "human_data": [
            "human_roleplay_data/data/participants.csv",
            "human_roleplay_data/data/responses.csv",
        ],
        "generated_outputs": "Run locally under generated_results/.",
    }
    (output_dir / "ANONYMOUS_RELEASE_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )


def audit(output_dir: Path, tokens: list[str]) -> None:
    command = [
        sys.executable,
        str(output_dir / "scripts" / AUDITOR.name),
        str(output_dir),
    ]
    for token in tokens:
        command.extend(["--forbidden-token", token])
    result = subprocess.run(
        command,
        cwd=output_dir,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    print(result.stdout, end="")
    if result.returncode:
        raise RuntimeError("Anonymous release audit failed")


def initialize_git(output_dir: Path) -> None:
    run(["git", "init", "-q", "-b", "main"], cwd=output_dir)
    run(["git", "add", "--all"], cwd=output_dir)
    anonymous_email = "authors" + chr(64) + "anonymous.invalid"
    identity = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Anonymous Authors",
        "GIT_AUTHOR_EMAIL": anonymous_email,
        "GIT_COMMITTER_NAME": "Anonymous Authors",
        "GIT_COMMITTER_EMAIL": anonymous_email,
    }
    run(
        [
            "git",
            "-c",
            "user.name=Anonymous Authors",
            "-c",
            f"user.email={anonymous_email}",
            "commit",
            "-q",
            "-m",
            "Anonymous review artifact",
        ],
        cwd=output_dir,
        env=identity,
    )


def write_archive(output_dir: Path, archive: Path) -> None:
    files = sorted(
        path
        for path in output_dir.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(output_dir).parts
    )
    with zipfile.ZipFile(
        archive,
        mode="x",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as handle:
        for path in files:
            relative = path.relative_to(output_dir).as_posix()
            info = zipfile.ZipInfo(relative)
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            mode = stat.S_IMODE(path.stat().st_mode)
            info.external_attr = (stat.S_IFREG | mode) << 16
            handle.writestr(info, path.read_bytes())


def main() -> None:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        raise SystemExit(f"Output directory already exists: {output_dir}")
    try:
        output_dir.relative_to(REPO_ROOT)
    except ValueError:
        pass
    else:
        raise SystemExit("Output directory must be outside the working repository")

    if args.archive is not None:
        archive = args.archive.resolve()
        if archive.exists():
            raise SystemExit(f"Archive already exists: {archive}")
        try:
            archive.relative_to(REPO_ROOT)
        except ValueError:
            pass
        else:
            raise SystemExit("Archive must be outside the working repository")
    else:
        archive = None

    if not args.allow_dirty:
        status = run(["git", "status", "--short", "--untracked-files=no"]).stdout
        if status.strip():
            raise SystemExit(
                "Tracked worktree is not clean. Commit first or use "
                "--allow-dirty only for a local test."
            )

    output_dir.mkdir(parents=True)
    try:
        included, omitted = copy_snapshot(output_dir)
        write_manifest(output_dir, included, omitted)
        audit(output_dir, args.forbidden_token)
        if args.init_git:
            initialize_git(output_dir)
        if archive is not None:
            archive.parent.mkdir(parents=True, exist_ok=True)
            write_archive(output_dir, archive)
    except Exception:
        print(
            f"Export failed; inspect and remove incomplete directory: {output_dir}",
            file=sys.stderr,
        )
        raise

    print(f"Anonymous snapshot: {output_dir}")
    print(f"Included tracked files: {included}; policy omissions: {omitted}")
    if args.init_git:
        print("Initialized one anonymous commit with no remote.")
    if archive is not None:
        print(f"Deterministic archive: {archive}")


if __name__ == "__main__":
    main()
