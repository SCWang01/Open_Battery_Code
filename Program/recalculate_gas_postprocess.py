"""Recalculate gas cost and carbon from saved battery-study Results.

This command is deliberately post-processing only.  It reads the already
optimized hourly gas and battery series, evaluates them against an alternate
monthly merit-order stack, and writes a parallel Results directory.  It never
imports or invokes the optimization model.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from analyze_summary import analyze_summary


PROGRAM_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PROGRAM_DIR.parent
DEFAULT_SOURCE_RESULTS_DIR = PROJECT_ROOT / "Results"
DEFAULT_STACK_DIR = PROJECT_ROOT / "data" / "ng_cost"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "Results_cc_grouped"
DEFAULT_SUMMARY_NAME = "summary_202301_202512_exact_V5_k20.csv"
DEFAULT_MAY_SUMMARY_NAME = "summary_202505_exact_V5_k20.csv"
DEFAULT_ANALYSIS_NAME = "analysis_202301_202512.xlsx"

CARBON_FACTOR_MTCO2_PER_MMBTU = 0.05306
MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
RESULT_FILE_PATTERN = re.compile(
    rf"^({'|'.join(MONTH_NAMES)})(202[3-5])_eta95%_std2_exact_V5_k20\.csv$"
)
STACK_FILE_NAME = "CAISO_NG_Final_{year:04d}_{month:02d}.xlsx"

INPUT_RESULT_COLUMNS = (
    "P_natural_gas",
    "P_natural_gas_actual",
    "P_ESS_actual",
)
HOURLY_OUTPUT_COLUMNS = (
    "marginal_price_gas",
    "marginal_price_gas_actual",
    "marginal_price_gas_withoutESS",
    "cost",
    "cost_actual",
    "cost_withoutESS",
    "carbon",
    "carbon_actual",
    "carbon_withoutESS",
)
TOTAL_OUTPUT_COLUMNS = (
    "total_cost",
    "total_cost_actual",
    "total_cost_withoutESS",
    "total_carbon",
    "total_carbon_actual",
    "total_carbon_withoutESS",
)
RECOMPUTED_RESULT_COLUMNS = frozenset(
    HOURLY_OUTPUT_COLUMNS + TOTAL_OUTPUT_COLUMNS
)


@dataclass(frozen=True)
class MeritOrderStack:
    """Validated cumulative cost and carbon arrays for one month."""

    capacity: np.ndarray
    price: np.ndarray
    carbon_intensity: np.ndarray
    cumulative_capacity: np.ndarray
    lower_capacity: np.ndarray
    cumulative_cost: np.ndarray
    cumulative_carbon: np.ndarray


@dataclass(frozen=True)
class MonthPostprocessResult:
    """Audit values for one recalculated monthly Results file."""

    year_month: str
    source_file: str
    output_file: str
    hours: int
    total_cost: float
    total_cost_actual: float
    total_carbon: float
    total_carbon_actual: float
    unchanged_column_count: int


def require_columns(
    frame: pd.DataFrame, required: tuple[str, ...], source: Path | str
) -> None:
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise KeyError(f"{source} is missing columns: {', '.join(missing)}")


def load_merit_order_stack(path: Path) -> MeritOrderStack:
    """Load one alternate stack using the canonical cost-ranked semantics."""
    if not path.is_file():
        raise FileNotFoundError(f"Grouped natural-gas stack not found: {path}")
    frame = pd.read_excel(path, engine="openpyxl")
    required = ("capacity", "$_per_mwh", "mmbtu_per_mwh")
    require_columns(frame, required, path)
    supply = frame[list(required)].copy()
    for column in required:
        supply[column] = pd.to_numeric(supply[column], errors="coerce")
    supply = supply.replace([np.inf, -np.inf], np.nan).dropna()
    supply = supply[supply["capacity"] > 0]
    supply = supply.sort_values("$_per_mwh", kind="stable")
    invalid = supply["mmbtu_per_mwh"].le(0) | supply["$_per_mwh"].le(0)
    if supply.empty or invalid.any():
        raise ValueError(f"Invalid positive cost/carbon stack values in {path}")

    capacity = supply["capacity"].to_numpy(float)
    price = supply["$_per_mwh"].to_numpy(float)
    carbon_intensity = (
        supply["mmbtu_per_mwh"].to_numpy(float)
        * CARBON_FACTOR_MTCO2_PER_MMBTU
    )
    cumulative_capacity = np.cumsum(capacity)
    lower_capacity = np.concatenate(([0.0], cumulative_capacity[:-1]))
    cumulative_cost = np.concatenate(
        ([0.0], np.cumsum(capacity * price)[:-1])
    )
    cumulative_carbon = np.concatenate(
        ([0.0], np.cumsum(capacity * carbon_intensity)[:-1])
    )
    return MeritOrderStack(
        capacity=capacity,
        price=price,
        carbon_intensity=carbon_intensity,
        cumulative_capacity=cumulative_capacity,
        lower_capacity=lower_capacity,
        cumulative_cost=cumulative_cost,
        cumulative_carbon=cumulative_carbon,
    )


def evaluate_stack(
    generation: np.ndarray, stack: MeritOrderStack
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return total fuel cost, marginal price, and direct CO2 by time step."""
    gas = np.asarray(generation, dtype=float)
    if not np.isfinite(gas).all():
        raise ValueError("Natural-gas generation contains non-finite values")
    index = np.clip(
        np.searchsorted(stack.cumulative_capacity, gas, side="left"),
        0,
        len(stack.capacity) - 1,
    )
    positive = gas > 0
    dispatched_in_bucket = gas - stack.lower_capacity[index]
    cost = np.where(
        positive,
        stack.cumulative_cost[index]
        + dispatched_in_bucket * stack.price[index],
        0.0,
    )
    marginal_price = np.where(positive, stack.price[index], 0.0)
    carbon = np.where(
        positive,
        stack.cumulative_carbon[index]
        + dispatched_in_bucket * stack.carbon_intensity[index],
        0.0,
    )
    return cost, marginal_price, carbon


def assert_unchanged_columns(
    source: pd.DataFrame, output: pd.DataFrame, source_name: str
) -> int:
    """Prove that post-processing did not alter optimization outputs."""
    columns = [
        column for column in source.columns if column not in RECOMPUTED_RESULT_COLUMNS
    ]
    for column in columns:
        left = source[column]
        right = output[column]
        if pd.api.types.is_numeric_dtype(left):
            if not np.allclose(
                pd.to_numeric(left, errors="coerce"),
                pd.to_numeric(right, errors="coerce"),
                equal_nan=True,
                rtol=0.0,
                atol=0.0,
            ):
                raise AssertionError(
                    f"Post-processing changed protected numeric column "
                    f"{column!r} in {source_name}"
                )
        elif not left.fillna("<NA>").equals(right.fillna("<NA>")):
            raise AssertionError(
                f"Post-processing changed protected column {column!r} "
                f"in {source_name}"
            )
    return len(columns)


def recalculate_result_frame(
    source: pd.DataFrame, stack: MeritOrderStack, source_name: str
) -> tuple[pd.DataFrame, dict[str, float], int]:
    """Replace only gas cost, marginal price, carbon, and their totals."""
    require_columns(source, INPUT_RESULT_COLUMNS, source_name)
    result = source.copy()
    gas_method = pd.to_numeric(source["P_natural_gas"], errors="coerce").to_numpy(float)
    gas_actual = pd.to_numeric(
        source["P_natural_gas_actual"], errors="coerce"
    ).to_numpy(float)
    battery_actual = pd.to_numeric(
        source["P_ESS_actual"], errors="coerce"
    ).to_numpy(float)
    gas_without_ess = np.maximum(gas_actual + battery_actual, 0.0)

    cost_method, marginal_method, carbon_method = evaluate_stack(gas_method, stack)
    cost_actual, marginal_actual, carbon_actual = evaluate_stack(gas_actual, stack)
    cost_no_ess, marginal_no_ess, carbon_no_ess = evaluate_stack(
        gas_without_ess, stack
    )
    result["marginal_price_gas"] = marginal_method
    result["marginal_price_gas_actual"] = marginal_actual
    result["marginal_price_gas_withoutESS"] = marginal_no_ess
    result["cost"] = cost_method
    result["cost_actual"] = cost_actual
    result["cost_withoutESS"] = cost_no_ess
    result["carbon"] = carbon_method
    result["carbon_actual"] = carbon_actual
    result["carbon_withoutESS"] = carbon_no_ess

    totals = {
        "total_cost": float(cost_method.sum()),
        "total_cost_actual": float(cost_actual.sum()),
        "total_cost_withoutESS": float(cost_no_ess.sum()),
        "total_carbon": float(carbon_method.sum()),
        "total_carbon_actual": float(carbon_actual.sum()),
        "total_carbon_withoutESS": float(carbon_no_ess.sum()),
    }
    for column, value in totals.items():
        result[column] = value
    unchanged_count = assert_unchanged_columns(source, result, source_name)
    return result, totals, unchanged_count


def write_csv_atomically(frame: pd.DataFrame, output_file: Path) -> None:
    """Replace one CSV only after a complete UTF-8 file has been saved."""
    output_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{output_file.stem}_",
            suffix=".csv",
            dir=output_file.parent,
            delete=False,
        ) as temporary_file:
            temporary_name = temporary_file.name
        frame.to_csv(temporary_name, index=False)
        os.replace(temporary_name, output_file)
    except Exception:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
        raise


def sha256(path: Path) -> str:
    """Return a streaming SHA-256 digest for provenance records."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover_result_files(source_dir: Path) -> dict[str, Path]:
    """Find exactly one canonical monthly Results CSV for every 2023-2025 month."""
    discovered: dict[str, Path] = {}
    month_numbers = {name: index for index, name in enumerate(MONTH_NAMES, start=1)}
    for path in source_dir.glob("*.csv"):
        match = RESULT_FILE_PATTERN.fullmatch(path.name)
        if match is None:
            continue
        month_name, year_text = match.groups()
        code = f"{year_text}{month_numbers[month_name]:02d}"
        if code in discovered:
            raise ValueError(f"Duplicate monthly Results for {code}")
        discovered[code] = path
    expected = {f"{year}{month:02d}" for year in range(2023, 2026) for month in range(1, 13)}
    missing = sorted(expected - discovered.keys())
    extra = sorted(discovered.keys() - expected)
    if missing or extra:
        raise ValueError(f"Monthly Results coverage mismatch; missing={missing}, extra={extra}")
    return discovered


def update_summary_file(
    source_path: Path,
    output_path: Path,
    totals_by_month: dict[str, dict[str, float]],
) -> None:
    """Update gas-derived totals while preserving every optimization metric."""
    frame = pd.read_csv(source_path, dtype={"year_month": str})
    require_columns(frame, ("year_month",), source_path)
    for index, row in frame.iterrows():
        month = str(row["year_month"]).replace(".0", "").zfill(6)
        if month not in totals_by_month:
            raise KeyError(f"No recalculated totals for summary month {month}")
        totals = totals_by_month[month]
        for column, value in totals.items():
            if column in frame.columns:
                frame.at[index, column] = value
        carbon_reduce = totals["total_carbon_actual"] - totals["total_carbon"]
        if "carbon_reduce" in frame.columns:
            frame.at[index, "carbon_reduce"] = carbon_reduce
        if "rate_carbon" in frame.columns:
            denominator = totals["total_carbon_actual"]
            frame.at[index, "rate_carbon"] = (
                carbon_reduce / denominator if denominator else 0.0
            )
    write_csv_atomically(frame, output_path)


def write_json_atomically(value: object, output_file: Path) -> None:
    """Write one JSON manifest atomically."""
    output_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{output_file.stem}_",
            suffix=".json",
            dir=output_file.parent,
            delete=False,
            mode="w",
            encoding="utf-8",
        ) as temporary_file:
            temporary_name = temporary_file.name
            json.dump(value, temporary_file, indent=2, ensure_ascii=False)
        os.replace(temporary_name, output_file)
    except Exception:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
        raise


def postprocess_all(
    source_results_dir: Path,
    stack_dir: Path,
    output_dir: Path,
) -> list[MonthPostprocessResult]:
    """Recalculate 36 months, summaries, and the Figure-4d analysis workbook."""
    for path in (source_results_dir, stack_dir):
        if not path.is_dir():
            raise FileNotFoundError(f"Required input directory not found: {path}")
    if output_dir.resolve() == source_results_dir.resolve():
        raise ValueError("Sensitivity post-processing must not overwrite Results")

    result_files = discover_result_files(source_results_dir)
    totals_by_month: dict[str, dict[str, float]] = {}
    audit_results: list[MonthPostprocessResult] = []
    manifest_files: list[dict[str, str]] = []

    for code, source_path in sorted(result_files.items()):
        year, month = int(code[:4]), int(code[4:])
        stack_path = stack_dir / STACK_FILE_NAME.format(year=year, month=month)
        stack = load_merit_order_stack(stack_path)
        source = pd.read_csv(source_path)
        result, totals, unchanged_count = recalculate_result_frame(
            source, stack, source_path.name
        )
        output_path = output_dir / source_path.name
        write_csv_atomically(result, output_path)
        totals_by_month[code] = totals
        audit_results.append(
            MonthPostprocessResult(
                year_month=code,
                source_file=str(source_path),
                output_file=str(output_path),
                hours=len(result),
                total_cost=totals["total_cost"],
                total_cost_actual=totals["total_cost_actual"],
                total_carbon=totals["total_carbon"],
                total_carbon_actual=totals["total_carbon_actual"],
                unchanged_column_count=unchanged_count,
            )
        )
        manifest_files.append(
            {
                "year_month": code,
                "source_sha256": sha256(source_path),
                "stack_sha256": sha256(stack_path),
                "output_sha256": sha256(output_path),
            }
        )

    summary_source = source_results_dir / DEFAULT_SUMMARY_NAME
    summary_output = output_dir / DEFAULT_SUMMARY_NAME
    update_summary_file(summary_source, summary_output, totals_by_month)
    may_summary_source = source_results_dir / DEFAULT_MAY_SUMMARY_NAME
    if may_summary_source.is_file():
        update_summary_file(
            may_summary_source,
            output_dir / DEFAULT_MAY_SUMMARY_NAME,
            {"202505": totals_by_month["202505"]},
        )
    analysis_output = output_dir / DEFAULT_ANALYSIS_NAME
    analyze_summary(summary_output, analysis_output)

    audit_frame = pd.DataFrame([asdict(item) for item in audit_results])
    write_csv_atomically(
        audit_frame, output_dir / "cc_grouped_postprocess_audit.csv"
    )
    write_json_atomically(
        {
            "contract": "postprocess_only_no_optimization",
            "source_results_dir": str(source_results_dir.resolve()),
            "stack_dir": str(stack_dir.resolve()),
            "output_dir": str(output_dir.resolve()),
            "months": manifest_files,
        },
        output_dir / "cc_grouped_postprocess_manifest.json",
    )
    return audit_results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Recalculate gas cost/carbon from saved Results only."
    )
    parser.add_argument(
        "--source-results-dir", type=Path, default=DEFAULT_SOURCE_RESULTS_DIR
    )
    parser.add_argument("--stack-dir", type=Path, default=DEFAULT_STACK_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser


def main() -> None:
    arguments = build_parser().parse_args()
    results = postprocess_all(
        arguments.source_results_dir,
        arguments.stack_dir,
        arguments.output_dir,
    )
    print(f"Recalculated {len(results)} monthly Results in {arguments.output_dir}")
    print(
        "Protected non-gas columns verified unchanged: "
        f"{min(item.unchanged_column_count for item in results)} per file"
    )
    print(
        "Analysis workbook: "
        f"{arguments.output_dir / DEFAULT_ANALYSIS_NAME}"
    )


if __name__ == "__main__":
    main()
