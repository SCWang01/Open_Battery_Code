"""Run reproducible V5 uncertainty comparisons and organize their artifacts.

The public command requires one or both experiment methods. Each requested
``meanstd`` value runs the complete configured month range through the matching
distributed driver, validates its aggregate CSV, invokes ``analyze_summary.py``,
and records resumable metadata in ``run_metadata.json``.

Examples::

    python Uncertain_Compare.py --methods self-scheduling --workers 12
    python Uncertain_Compare.py --methods self-scheduling bidding --workers 12
    python Uncertain_Compare.py --methods self-scheduling bidding --workers 12 --resume
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence


PROGRAM_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PROGRAM_DIR.parent
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "Uncertainties_Comparison"
RANDOM_MANIFEST_PATH = PROJECT_ROOT / "data" / "random_data" / "manifest.json"
ANALYSIS_SCRIPT = PROGRAM_DIR / "analyze_summary.py"
DEFAULT_MEANSTDS = (2.0, 4.0, 6.0, 8.0, 10.0)
DEFAULT_WORKERS = 12
METADATA_FILENAME = "run_metadata.json"
METADATA_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class MethodSpec:
    """Files and labels that define one supported experiment method."""

    name: str
    directory_name: str
    analysis_label: str
    driver_path: Path


METHOD_SPECS = {
    spec.name: spec
    for spec in (
        MethodSpec(
            name="self-scheduling",
            directory_name="self-scheduling",
            analysis_label="self_scheduling",
            driver_path=PROGRAM_DIR / "distributed_V5_self_scheduling.py",
        ),
        MethodSpec(
            name="bidding",
            directory_name="bidding",
            analysis_label="bidding",
            driver_path=PROGRAM_DIR / "distributed_V5_case_study.py",
        ),
    )
}


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line contract for uncertainty comparisons."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--methods",
        nargs="+",
        required=True,
        choices=tuple(METHOD_SPECS),
        help="methods to run, in execution order",
    )
    parser.add_argument(
        "--meanstds",
        nargs="+",
        type=float,
        default=list(DEFAULT_MEANSTDS),
        help="prediction-error percentages (default: 2 4 6 8 10)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"parallel month workers (default: {DEFAULT_WORKERS})",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help=f"comparison output root (default: {DEFAULT_OUTPUT_ROOT})",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="skip validated completed methods and rerun incomplete methods",
    )
    parser.add_argument(
        "--skip-smoke-test",
        action="store_true",
        help="skip the temporary one-month integration smoke test",
    )
    return parser


def normalize_methods(values: Sequence[str]) -> tuple[str, ...]:
    """Deduplicate methods while preserving the requested execution order."""
    methods = tuple(dict.fromkeys(values))
    if not methods:
        raise ValueError("--methods must contain at least one method")
    invalid = [method for method in methods if method not in METHOD_SPECS]
    if invalid:
        raise ValueError(f"unsupported methods: {invalid}")
    return methods


def normalize_meanstds(values: Sequence[float]) -> tuple[float, ...]:
    """Validate and deduplicate finite non-negative percentages."""
    normalized = []
    for index, raw_value in enumerate(values):
        value = float(raw_value)
        if not math.isfinite(value) or value < 0:
            raise ValueError(
                f"--meanstds value at index {index} must be finite and non-negative: "
                f"{raw_value!r}"
            )
        if value not in normalized:
            normalized.append(value)
    if not normalized:
        raise ValueError("--meanstds must contain at least one value")
    return tuple(normalized)


def validate_workers(value: int) -> int:
    """Return a positive worker count."""
    if value < 1:
        raise ValueError("--workers must be at least 1")
    return value


def load_random_manifest(path: Path = RANDOM_MANIFEST_PATH) -> dict:
    """Read and validate the random-scenario facts used for provenance."""
    if not path.is_file():
        raise FileNotFoundError(f"random-data manifest does not exist: {path}")
    with path.open("r", encoding="utf-8") as source:
        manifest = json.load(source)

    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError(f"random-data manifest has no file entries: {path}")
    months = [entry.get("year_month") for entry in files]
    if any(not isinstance(month, str) or len(month) != 6 for month in months):
        raise ValueError(f"random-data manifest contains invalid months: {path}")
    if len(months) != len(set(months)):
        raise ValueError(f"random-data manifest contains duplicate months: {path}")
    if manifest.get("seed") is None:
        raise ValueError(f"random-data manifest does not declare a seed: {path}")
    return manifest


def meanstd_label(value: float) -> str:
    """Return a stable path-safe label such as ``2`` or ``2_5``."""
    return format(value, ".15g").replace(".", "_")


def experiment_paths(
    output_root: Path,
    meanstd: float,
    method: str,
) -> tuple[Path, Path, Path, Path]:
    """Derive the parameter directory, method directory, analysis, and metadata."""
    label = meanstd_label(meanstd)
    parameter_dir = output_root / f"meanstd_{label}"
    method_spec = METHOD_SPECS[method]
    method_dir = parameter_dir / method_spec.directory_name
    analysis_path = (
        parameter_dir
        / f"summary_meanstd_{label}_{method_spec.analysis_label}.xlsx"
    )
    metadata_path = parameter_dir / METADATA_FILENAME
    return parameter_dir, method_dir, analysis_path, metadata_path


def build_driver_command(
    method: str,
    meanstd: float,
    workers: int,
    output_dir: Path,
    months: Sequence[int] | None = None,
) -> list[str]:
    """Assemble one explicit distributed-driver command."""
    command = [
        sys.executable,
        str(METHOD_SPECS[method].driver_path),
        "--workers",
        str(workers),
        "--meanstd",
        format(meanstd, ".15g"),
        "--output-dir",
        str(output_dir),
    ]
    if months is not None:
        command.extend(["--months", *(str(month) for month in months)])
    return command


def run_command(command: Sequence[str]) -> None:
    """Run one child command and fail immediately on a nonzero exit code."""
    print(f"Running: {subprocess.list2cmdline(list(command))}", flush=True)
    subprocess.run(list(command), cwd=PROGRAM_DIR, check=True)


def read_summary_rows(path: Path) -> list[dict[str, str]]:
    """Read one aggregate summary CSV."""
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


def validate_summary(
    path: Path,
    expected_months: Sequence[str],
    expected_meanstd: float,
) -> Path:
    """Require exact month coverage and the requested meanstd provenance."""
    rows = read_summary_rows(path)
    actual_months = [row.get("year_month", "") for row in rows]
    if actual_months != list(expected_months):
        raise ValueError(
            f"summary month coverage is invalid in {path}: "
            f"expected {list(expected_months)}, got {actual_months}"
        )
    if "meanstd" not in (rows[0] if rows else {}):
        raise ValueError(f"summary has no meanstd provenance column: {path}")
    for row_number, row in enumerate(rows, start=2):
        try:
            actual_meanstd = float(row["meanstd"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"summary row {row_number} has invalid meanstd in {path}"
            ) from exc
        if not math.isclose(actual_meanstd, expected_meanstd, abs_tol=1e-12):
            raise ValueError(
                f"summary row {row_number} has meanstd={actual_meanstd}, "
                f"expected {expected_meanstd}: {path}"
            )
    return path


def find_valid_summary(
    output_dir: Path,
    expected_months: Sequence[str],
    expected_meanstd: float,
) -> Path:
    """Find the one aggregate CSV that satisfies the experiment contract."""
    candidates = sorted(output_dir.glob("summary_*.csv"))
    valid = []
    failures = []
    for candidate in candidates:
        try:
            valid.append(
                validate_summary(candidate, expected_months, expected_meanstd)
            )
        except (OSError, ValueError) as exc:
            failures.append(f"{candidate.name}: {exc}")
    if len(valid) != 1:
        details = "; ".join(failures) if failures else "no summary CSV candidates"
        raise ValueError(
            f"expected exactly one valid summary in {output_dir}, found {len(valid)}; "
            f"{details}"
        )
    return valid[0]


def write_smoke_analysis_input(source_summary: Path, destination: Path) -> None:
    """Expand one real smoke row into a synthetic complete year for analyzer QA."""
    rows = read_summary_rows(source_summary)
    if len(rows) != 1:
        raise ValueError(f"smoke summary must contain exactly one row: {source_summary}")
    fieldnames = list(rows[0])
    synthetic_rows = []
    for month in range(1, 13):
        row = dict(rows[0])
        row["year_month"] = f"2023{month:02d}"
        synthetic_rows.append(row)
    with destination.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(synthetic_rows)


def run_smoke_tests(methods: Sequence[str]) -> None:
    """Run one real month per method and verify analyzer integration in temp."""
    smoke_meanstd = DEFAULT_MEANSTDS[0]
    with tempfile.TemporaryDirectory(prefix="uncertainty_smoke_") as directory:
        smoke_root = Path(directory)
        for method in methods:
            print(f"Smoke testing {method} at meanstd={smoke_meanstd:g}...", flush=True)
            method_dir = smoke_root / method
            run_command(
                build_driver_command(
                    method,
                    smoke_meanstd,
                    workers=1,
                    output_dir=method_dir,
                    months=[0],
                )
            )
            source_summary = find_valid_summary(
                method_dir,
                expected_months=["202301"],
                expected_meanstd=smoke_meanstd,
            )
            analyzer_input = smoke_root / f"{method}_synthetic_year.csv"
            analyzer_output = smoke_root / f"{method}_analysis.xlsx"
            write_smoke_analysis_input(source_summary, analyzer_input)
            run_command(
                [
                    sys.executable,
                    str(ANALYSIS_SCRIPT),
                    str(analyzer_input),
                    "--output",
                    str(analyzer_output),
                ]
            )
            if not analyzer_output.is_file() or analyzer_output.stat().st_size == 0:
                raise RuntimeError(
                    f"smoke analysis workbook was not created: {analyzer_output}"
                )
    print("Smoke tests passed.", flush=True)


def now_iso() -> str:
    """Return a local timezone-aware timestamp for experiment metadata."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def write_json_atomic(path: Path, payload: dict) -> None:
    """Atomically replace a metadata JSON file in its destination directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary_path.open("w", encoding="utf-8", newline="\n") as target:
            json.dump(payload, target, indent=2, ensure_ascii=False)
            target.write("\n")
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def new_metadata(meanstd: float, workers: int, manifest: dict) -> dict:
    """Assemble a new parameter-level provenance record."""
    months = [entry["year_month"] for entry in manifest["files"]]
    return {
        "schema_version": METADATA_SCHEMA_VERSION,
        "meanstd": meanstd,
        "random_seed": manifest["seed"],
        "rng_algorithm": manifest.get("rng_algorithm"),
        "random_manifest": str(RANDOM_MANIFEST_PATH),
        "period": {"start": months[0], "end": months[-1], "month_count": len(months)},
        "workers": workers,
        "updated_at": now_iso(),
        "methods": {},
    }


def load_or_create_metadata(
    path: Path,
    meanstd: float,
    workers: int,
    manifest: dict,
    resume: bool,
) -> dict:
    """Load compatible resume metadata or create a fresh provenance record."""
    if not path.exists():
        return new_metadata(meanstd, workers, manifest)
    if not resume:
        raise FileExistsError(f"metadata already exists; use --resume: {path}")
    with path.open("r", encoding="utf-8") as source:
        metadata = json.load(source)
    if metadata.get("schema_version") != METADATA_SCHEMA_VERSION:
        raise ValueError(f"incompatible metadata schema in {path}")
    if not math.isclose(float(metadata.get("meanstd")), meanstd, abs_tol=1e-12):
        raise ValueError(f"metadata meanstd does not match meanstd={meanstd:g}: {path}")
    if metadata.get("random_seed") != manifest["seed"]:
        raise ValueError(f"metadata random seed does not match the manifest: {path}")
    metadata["workers"] = workers
    metadata["updated_at"] = now_iso()
    return metadata


def completed_method_is_valid(
    method_record: dict | None,
    expected_months: Sequence[str],
    meanstd: float,
) -> bool:
    """Return whether resume metadata points to complete, valid artifacts."""
    if not method_record or method_record.get("status") != "completed":
        return False
    try:
        validate_summary(
            Path(method_record["source_summary"]),
            expected_months,
            meanstd,
        )
        analysis_path = Path(method_record["analysis_workbook"])
        return analysis_path.is_file() and analysis_path.stat().st_size > 0
    except (KeyError, OSError, TypeError, ValueError):
        return False


def ensure_fresh_method_target(
    method_dir: Path,
    analysis_path: Path,
    resume: bool,
) -> None:
    """Refuse accidental replacement unless the caller explicitly resumes."""
    if resume:
        return
    method_has_files = method_dir.exists() and any(method_dir.iterdir())
    if method_has_files or analysis_path.exists():
        raise FileExistsError(
            f"experiment artifacts already exist; use --resume: {method_dir}"
        )


def run_method(
    method: str,
    meanstd: float,
    workers: int,
    output_root: Path,
    expected_months: Sequence[str],
    metadata: dict,
    metadata_path: Path,
    resume: bool,
) -> None:
    """Run, validate, analyze, and record one method for one meanstd value."""
    _, method_dir, analysis_path, _ = experiment_paths(
        output_root, meanstd, method
    )
    method_record = metadata["methods"].get(method)
    if resume and completed_method_is_valid(
        method_record, expected_months, meanstd
    ):
        print(f"Skipping completed {method} at meanstd={meanstd:g}.", flush=True)
        return

    ensure_fresh_method_target(method_dir, analysis_path, resume)
    command = build_driver_command(method, meanstd, workers, method_dir)
    metadata["methods"][method] = {
        "status": "running",
        "started_at": now_iso(),
        "output_dir": str(method_dir),
        "command": command,
    }
    metadata["updated_at"] = now_iso()
    write_json_atomic(metadata_path, metadata)

    try:
        run_command(command)
        source_summary = find_valid_summary(
            method_dir, expected_months, meanstd
        )
        run_command(
            [
                sys.executable,
                str(ANALYSIS_SCRIPT),
                str(source_summary),
                "--output",
                str(analysis_path),
            ]
        )
        if not analysis_path.is_file() or analysis_path.stat().st_size == 0:
            raise RuntimeError(f"analysis workbook was not created: {analysis_path}")
    except BaseException as exc:
        metadata["methods"][method].update(
            {
                "status": "failed",
                "failed_at": now_iso(),
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        metadata["updated_at"] = now_iso()
        write_json_atomic(metadata_path, metadata)
        raise

    metadata["methods"][method].update(
        {
            "status": "completed",
            "completed_at": now_iso(),
            "source_summary": str(source_summary),
            "analysis_workbook": str(analysis_path),
        }
    )
    metadata["updated_at"] = now_iso()
    write_json_atomic(metadata_path, metadata)


def run_comparisons(
    methods: Sequence[str],
    meanstds: Sequence[float],
    workers: int,
    output_root: Path,
    resume: bool = False,
    smoke_test: bool = True,
) -> None:
    """Execute the validated uncertainty-comparison workflow."""
    methods = normalize_methods(methods)
    meanstds = normalize_meanstds(meanstds)
    workers = validate_workers(workers)
    output_root = Path(output_root).resolve()
    manifest = load_random_manifest()
    expected_months = [entry["year_month"] for entry in manifest["files"]]

    if smoke_test:
        run_smoke_tests(methods)

    for meanstd in meanstds:
        parameter_dir, _, _, metadata_path = experiment_paths(
            output_root, meanstd, methods[0]
        )
        parameter_dir.mkdir(parents=True, exist_ok=True)
        metadata = load_or_create_metadata(
            metadata_path,
            meanstd,
            workers,
            manifest,
            resume,
        )
        for method in methods:
            print(f"Starting {method} at meanstd={meanstd:g}...", flush=True)
            run_method(
                method,
                meanstd,
                workers,
                output_root,
                expected_months,
                metadata,
                metadata_path,
                resume,
            )
    print("All requested uncertainty comparisons completed.", flush=True)


def main(argv: Sequence[str] | None = None) -> None:
    """Parse CLI arguments and run the requested comparison methods."""
    args = build_parser().parse_args(argv)
    try:
        run_comparisons(
            methods=args.methods,
            meanstds=args.meanstds,
            workers=args.workers,
            output_root=args.output_root,
            resume=args.resume,
            smoke_test=not args.skip_smoke_test,
        )
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        raise SystemExit(f"error: {exc}") from exc


if __name__ == "__main__":
    main()
