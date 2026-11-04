"""Hourly ERCOT benchmarks sharing V5-style telemetry-reconstructed ESS parameters.

Each participating hour clears its observed source TPO curves at the same hourly
mean LMP, averages their raw MW, and applies one hourly energy/power constraint.
Wholly empty curves use the same member's latest earlier valid curve, including
across hours/days/months, as an explicit backtest assumption; provenance is exported.
Piecewise and Discrete retain independent MWh trajectories. Run without arguments
to load experiment_config_1hour.json; monthly outputs live directly in Results/Micro_Results/benchmark with YYYYMM filenames.

"""

from __future__ import annotations

import argparse
import json
import math
import multiprocessing as mp
import os
import tempfile
from bisect import bisect_right
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd
from openpyxl.styles import Alignment
from openpyxl.utils import get_column_letter

import Experiment_Data_1hour as experiment_data


from project_paths import MICRO_DATA_DIR, MICRO_RESULTS_DIR

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = MICRO_DATA_DIR / "Six_Month_Data"
RESULTS_DIR = MICRO_RESULTS_DIR / "benchmark"

TIME_COLUMN = "SCED Time Stamp"
RESOURCE_COLUMN = "Resource Name"
LMP_COLUMN = "LMP"
HSL_COLUMN = "HSL"
LSL_COLUMN = "LSL"
SOC_COLUMN = "State of Charge"
MINIMUM_SOC_COLUMN = "Minimum SOC"
MAXIMUM_SOC_COLUMN = "Maximum SOC"
APPLIED_MINIMUM_SOC_COLUMN = "Applied Minimum SOC"
APPLIED_MAXIMUM_SOC_COLUMN = "Applied Maximum SOC"
RAW_CLEARED_MW_COLUMN = "Raw Cleared MW"
POWER_CONSTRAINED_MW_COLUMN = "HSL/LSL-Constrained MW"
POWER_LIMITED_COLUMN = "HSL/LSL Limited"
MODELED_SOC_BEFORE_COLUMN = "Modeled SOC Before"
EXECUTED_MW_COLUMN = "Executed MW"
SOC_LIMITED_COLUMN = "SOC Limited"
MODELED_SOC_AFTER_COLUMN = "Modeled SOC After"
CURVE_COLUMN = "curve"
PROFIT_COLUMN = "Profit"
OUTPUT_TIME_COLUMN = "Time"
OUTPUT_COLUMNS = (
    OUTPUT_TIME_COLUMN,
    RESOURCE_COLUMN,
    PROFIT_COLUMN,
    LMP_COLUMN,
    HSL_COLUMN,
    LSL_COLUMN,
    RAW_CLEARED_MW_COLUMN,
    POWER_CONSTRAINED_MW_COLUMN,
    POWER_LIMITED_COLUMN,
    MODELED_SOC_BEFORE_COLUMN,
    APPLIED_MINIMUM_SOC_COLUMN,
    APPLIED_MAXIMUM_SOC_COLUMN,
    EXECUTED_MW_COLUMN,
    SOC_LIMITED_COLUMN,
    MODELED_SOC_AFTER_COLUMN,
    CURVE_COLUMN,
)
CURVE_COLUMN_PAIRS = tuple(
    (f"Submitted TPO-MW{index}", f"Submitted TPO-Price{index}")
    for index in range(1, 11)
)
INTERVALS_PER_HOUR = 1.0
ONE_WAY_EFFICIENCY = 0.95
NUMERICAL_TOLERANCE = 1e-9
INPUT_PATTERN = "*_Usage.csv"
TIME_FORMAT = "%m/%d/%Y %H:%M:%S"
EXCEL_TIME_FORMAT = "yyyy-mm-dd hh:mm:ss"
EXCEL_COLUMN_WIDTHS = {
    OUTPUT_TIME_COLUMN: 21,
    RESOURCE_COLUMN: 28,
    PROFIT_COLUMN: 22,
    LMP_COLUMN: 14,
    HSL_COLUMN: 14,
    LSL_COLUMN: 14,
    RAW_CLEARED_MW_COLUMN: 21,
    POWER_CONSTRAINED_MW_COLUMN: 25,
    POWER_LIMITED_COLUMN: 18,
    MODELED_SOC_BEFORE_COLUMN: 22,
    APPLIED_MINIMUM_SOC_COLUMN: 22,
    APPLIED_MAXIMUM_SOC_COLUMN: 22,
    EXECUTED_MW_COLUMN: 18,
    SOC_LIMITED_COLUMN: 14,
    MODELED_SOC_AFTER_COLUMN: 22,
    CURVE_COLUMN: 80,
}
NUMERIC_OUTPUT_COLUMNS = (
    PROFIT_COLUMN,
    LMP_COLUMN,
    HSL_COLUMN,
    LSL_COLUMN,
    RAW_CLEARED_MW_COLUMN,
    POWER_CONSTRAINED_MW_COLUMN,
    MODELED_SOC_BEFORE_COLUMN,
    APPLIED_MINIMUM_SOC_COLUMN,
    APPLIED_MAXIMUM_SOC_COLUMN,
    EXECUTED_MW_COLUMN,
    MODELED_SOC_AFTER_COLUMN,
)

BidCurve = tuple[tuple[float, float], ...]
CURVE_POLICY = "whole_empty_curve_carry_forward_last_valid"
DEFAULT_WORKERS = 12


@dataclass(frozen=True)
class MarketEvent:
    """One timestamped curve, optional LMP, and operating limits."""

    timestamp: datetime
    curve: BidCurve
    lmp: float | None
    hsl: float
    lsl: float
    state_of_charge: float
    minimum_soc: float
    maximum_soc: float
    source_row: int
    sced_timestamp: datetime | None = None
    status: str = "ON"
    raw_hsl: float | None = None
    raw_lsl: float | None = None
    hourly_curves: tuple[BidCurve, ...] = ()
    metadata: dict | None = None


@dataclass(frozen=True)
class ResourceData:
    """Validated events belonging to exactly one ERCOT resource."""

    name: str
    events: tuple[MarketEvent, ...]
    experimental: bool = False


@dataclass(frozen=True)
class BenchmarkSummary:
    """Files written and resources skipped because every LMP was missing."""

    output_paths: tuple[Path, ...]
    skipped_resources: tuple[str, ...]


@dataclass(frozen=True)
class BenchmarkMemberResult:
    """Worker-owned computation returned for deterministic parent-side export."""

    name: str
    audit: dict
    parameters: dict
    frames: tuple[pd.DataFrame, ...] = ()


@dataclass(frozen=True)
class DispatchStep:
    """Auditable result of applying power and SOC limits to one dispatch."""

    raw_mw: float
    power_constrained_mw: float
    power_limited: bool
    soc_before: float
    minimum_soc: float
    maximum_soc: float
    executed_mw: float
    soc_limited: bool
    soc_after: float


def _parse_finite_float(
    value: str | None,
    *,
    path: Path,
    row_number: int,
    column: str,
) -> float:
    text = (value or "").strip()
    try:
        number = float(text)
    except ValueError as exc:
        raise ValueError(
            f"{path.name}:{row_number}: invalid {column!r}: {text!r}"
        ) from exc
    if not math.isfinite(number):
        raise ValueError(f"{path.name}:{row_number}: non-finite {column!r}: {text!r}")
    return number


def _parse_curve(row: dict[str, str | None], path: Path, row_number: int) -> BidCurve:
    points: list[tuple[float, float]] = []
    for mw_column, price_column in CURVE_COLUMN_PAIRS:
        mw_text = (row.get(mw_column) or "").strip()
        price_text = (row.get(price_column) or "").strip()
        if not mw_text and not price_text:
            continue
        if not mw_text or not price_text:
            raise ValueError(
                f"{path.name}:{row_number}: {mw_column!r} and "
                f"{price_column!r} must both be populated or both be blank"
            )
        mw = _parse_finite_float(
            mw_text,
            path=path,
            row_number=row_number,
            column=mw_column,
        )
        price = _parse_finite_float(
            price_text,
            path=path,
            row_number=row_number,
            column=price_column,
        )
        points.append((mw, price))

    if not points:
        raise ValueError(f"{path.name}:{row_number}: submitted TPO curve is empty")

    for point_index in range(1, len(points)):
        previous_mw, previous_price = points[point_index - 1]
        mw, price = points[point_index]
        if mw < previous_mw:
            raise ValueError(
                f"{path.name}:{row_number}: curve MW decreases between points "
                f"{point_index} and {point_index + 1}"
            )
        if price < previous_price:
            raise ValueError(
                f"{path.name}:{row_number}: curve price decreases between points "
                f"{point_index} and {point_index + 1}"
            )
    return tuple(points)


def _piecewise_cleared_quantity(curve: BidCurve, lmp: float) -> float:
    """Clear by linear interpolation, clamping beyond both curve endpoints."""

    prices = tuple(price for _, price in curve)
    insertion = bisect_right(prices, lmp)
    if insertion == 0:
        return curve[0][0]

    # At a repeated bid price, all quantities offered at LMP are accepted.
    if prices[insertion - 1] == lmp:
        return curve[insertion - 1][0]
    if insertion == len(curve):
        return curve[-1][0]

    lower_mw, lower_price = curve[insertion - 1]
    upper_mw, upper_price = curve[insertion]
    fraction = (lmp - lower_price) / (upper_price - lower_price)
    return lower_mw + fraction * (upper_mw - lower_mw)


def _discrete_cleared_quantity(curve: BidCurve, lmp: float) -> float:
    """Clear the greatest MW whose bid price is no greater than LMP."""

    prices = tuple(price for _, price in curve)
    insertion = bisect_right(prices, lmp)
    if insertion == 0:
        return curve[0][0]
    return curve[insertion - 1][0]


def _clamp(value: float, lower: float, upper: float) -> float:
    """Project a scalar onto a validated closed interval."""

    return min(max(value, lower), upper)


def _values_differ(left: float, right: float) -> bool:
    return not math.isclose(
        left,
        right,
        rel_tol=0.0,
        abs_tol=NUMERICAL_TOLERANCE,
    )


def _snap_to_boundary(value: float, lower: float, upper: float) -> float:
    """Remove numerical residue at either edge of a closed interval."""

    if math.isclose(value, lower, rel_tol=0.0, abs_tol=NUMERICAL_TOLERANCE):
        return lower
    if math.isclose(value, upper, rel_tol=0.0, abs_tol=NUMERICAL_TOLERANCE):
        return upper
    return value


def _power_to_reach_soc(soc_before: float, target_soc: float) -> float:
    """Return signed MW that reaches a target SOC in one hourly step."""

    if target_soc >= soc_before:
        return -((target_soc - soc_before) * INTERVALS_PER_HOUR / ONE_WAY_EFFICIENCY)
    return (soc_before - target_soc) * INTERVALS_PER_HOUR * ONE_WAY_EFFICIENCY


def _soc_after_dispatch(soc_before: float, executed_mw: float) -> float:
    """Apply the signed-MW SOC transition for one hourly interval."""

    if executed_mw >= 0.0:
        discharge_mw = executed_mw
        return soc_before - (discharge_mw / ONE_WAY_EFFICIENCY / INTERVALS_PER_HOUR)

    charge_mw = -executed_mw
    return soc_before + (charge_mw * ONE_WAY_EFFICIENCY / INTERVALS_PER_HOUR)


def _constrain_dispatch(
    *,
    resource: str,
    event: MarketEvent,
    raw_mw: float,
    soc_before: float,
    minimum_soc: float,
    maximum_soc: float,
) -> DispatchStep:
    """Project raw clearing onto the joint power/SOC feasible interval."""

    power_constrained_mw = _clamp(raw_mw, event.lsl, event.hsl)

    # Signed MW is monotone decreasing in resulting SOC. The maximum-SOC
    # target therefore defines the lower MW edge and the minimum-SOC target
    # defines the upper MW edge, including when a changing bound requires
    # corrective charging or discharging.
    soc_feasible_lower = _power_to_reach_soc(soc_before, maximum_soc)
    soc_feasible_upper = _power_to_reach_soc(soc_before, minimum_soc)
    feasible_lower = max(event.lsl, soc_feasible_lower)
    feasible_upper = min(event.hsl, soc_feasible_upper)
    if feasible_lower > feasible_upper + NUMERICAL_TOLERANCE:
        raise ValueError(
            f"{resource} at {event.timestamp.isoformat(sep=' ')}: no executable "
            f"MW satisfies power interval [{event.lsl}, {event.hsl}] and SOC "
            f"interval [{minimum_soc}, {maximum_soc}] from SOC {soc_before}"
        )
    if feasible_lower > feasible_upper:
        boundary = (feasible_lower + feasible_upper) / 2.0
        feasible_lower = boundary
        feasible_upper = boundary

    executed_mw = _clamp(
        power_constrained_mw,
        feasible_lower,
        feasible_upper,
    )
    soc_after = _soc_after_dispatch(soc_before, executed_mw)
    if not (
        minimum_soc - NUMERICAL_TOLERANCE
        <= soc_after
        <= maximum_soc + NUMERICAL_TOLERANCE
    ):
        raise RuntimeError(
            f"{resource} at {event.timestamp.isoformat(sep=' ')}: internal SOC "
            f"constraint failure ({soc_after} outside "
            f"[{minimum_soc}, {maximum_soc}])"
        )
    soc_after = _snap_to_boundary(soc_after, minimum_soc, maximum_soc)

    return DispatchStep(
        raw_mw=raw_mw,
        power_constrained_mw=power_constrained_mw,
        power_limited=_values_differ(raw_mw, power_constrained_mw),
        soc_before=soc_before,
        minimum_soc=minimum_soc,
        maximum_soc=maximum_soc,
        executed_mw=executed_mw,
        soc_limited=_values_differ(power_constrained_mw, executed_mw),
        soc_after=soc_after,
    )


def _curve_as_json(curve: BidCurve) -> str:
    matrix = [[mw, price] for mw, price in curve]
    return json.dumps(matrix, separators=(",", ":"), ensure_ascii=False)


def _output_row(
    *,
    common: dict[str, object],
    lmp: float,
    step: DispatchStep,
) -> dict[str, object]:
    """Assemble one output record from a validated dispatch step."""

    return {
        **common,
        PROFIT_COLUMN: (lmp * step.executed_mw / INTERVALS_PER_HOUR),
        RAW_CLEARED_MW_COLUMN: step.raw_mw,
        POWER_CONSTRAINED_MW_COLUMN: step.power_constrained_mw,
        POWER_LIMITED_COLUMN: step.power_limited,
        MODELED_SOC_BEFORE_COLUMN: step.soc_before,
        APPLIED_MINIMUM_SOC_COLUMN: step.minimum_soc,
        APPLIED_MAXIMUM_SOC_COLUMN: step.maximum_soc,
        EXECUTED_MW_COLUMN: step.executed_mw,
        SOC_LIMITED_COLUMN: step.soc_limited,
        MODELED_SOC_AFTER_COLUMN: step.soc_after,
    }


def _build_output_frames(
    resource_data: ResourceData,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Average raw clearings first; enforce hourly feasibility exactly once."""
    rows = [[], []]
    energy = [resource_data.events[0].state_of_charge] * 2
    clearers = (_piecewise_cleared_quantity, _discrete_cleared_quantity)
    for event in resource_data.events:
        available = event.status == "AVAILABLE"
        common = {
            OUTPUT_TIME_COLUMN: event.timestamp,
            RESOURCE_COLUMN: resource_data.name,
            LMP_COLUMN: event.lmp,
            HSL_COLUMN: event.raw_hsl,
            LSL_COLUMN: event.raw_lsl,
            CURVE_COLUMN: json.dumps(event.hourly_curves, separators=(",", ":")),
            **event.metadata,
        }
        for index, clearer in enumerate(clearers):
            raw_mw = (
                sum(clearer(curve, event.lmp) for curve in event.hourly_curves)
                / len(event.hourly_curves)
                if available
                else 0.0
            )
            step = _constrain_dispatch(
                resource=resource_data.name,
                event=event,
                raw_mw=raw_mw,
                soc_before=energy[index],
                minimum_soc=event.minimum_soc,
                maximum_soc=event.maximum_soc,
            )
            record = _output_row(common=common, lmp=event.lmp, step=step)
            record["Normalized SOC Before"] = experiment_data.normalize_energy(
                step.soc_before, event.maximum_soc
            )
            record["Normalized SOC After"] = experiment_data.normalize_energy(
                step.soc_after, event.maximum_soc
            )
            rows[index].append(record)
            energy[index] = step.soc_after
    return tuple(pd.DataFrame(records) for records in rows)


def _write_excel_atomic(frame: pd.DataFrame, output_path: Path) -> None:
    """Write a complete workbook before atomically replacing any old result."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{output_path.stem}.",
        suffix=output_path.suffix,
        dir=output_path.parent,
        delete=False,
    ) as temporary:
        temporary_path = Path(temporary.name)

    try:
        with pd.ExcelWriter(
            temporary_path,
            engine="openpyxl",
            datetime_format=EXCEL_TIME_FORMAT,
        ) as writer:
            frame.to_excel(writer, sheet_name="Profit", index=False)
            worksheet = writer.sheets["Profit"]
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions
            output_columns = {
                cell.value: get_column_letter(cell.column) for cell in worksheet[1]
            }
            for column_name, width in EXCEL_COLUMN_WIDTHS.items():
                worksheet.column_dimensions[output_columns[column_name]].width = width
            for column_name in frame.columns:
                if column_name not in EXCEL_COLUMN_WIDTHS:
                    worksheet.column_dimensions[output_columns[column_name]].width = (
                        max(18, min(len(column_name) + 2, 34))
                    )
            for column_name in (
                experiment_data.STANDARD_TIME,
                experiment_data.SCED_TIME,
            ):
                if column_name in frame:
                    for cell in worksheet[output_columns[column_name]][1:]:
                        cell.number_format = EXCEL_TIME_FORMAT
            for cell in worksheet[output_columns[OUTPUT_TIME_COLUMN]][1:]:
                cell.number_format = EXCEL_TIME_FORMAT
            for column_name in NUMERIC_OUTPUT_COLUMNS:
                for cell in worksheet[output_columns[column_name]][1:]:
                    cell.number_format = "0.###############"
            for cell in worksheet[output_columns[CURVE_COLUMN]][1:]:
                cell.alignment = Alignment(wrap_text=False)
        os.replace(temporary_path, output_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def from_experiment_member(
    member: experiment_data.MemberData, steps: int
) -> ResourceData:
    """Resolve whole empty curves before averaging observed source clearings.

    Cache valid submissions even in unavailable hours. Never fill individual
    points, borrow a future curve, or replace a malformed nonempty submission.
    Cross-hour/day reuse is a backtest assumption, not proof of offer validity.
    """
    events = []
    previous: tuple[BidCurve, int, pd.Timestamp] | None = None
    for row in member.frame.iloc[:steps].to_dict(orient="records"):
        curves = []
        origins = []
        available = bool(row[experiment_data.AVAILABLE])
        if "Resolved Curves" in row:
            metadata = experiment_data.result_metadata(row)
            metadata.update(
                {
                    key: row[key]
                    for key in (
                        "Curve Policy",
                        "Curve Sources",
                        "Curve Carry Forward Count",
                    )
                }
            )
            events.append(
                MarketEvent(
                    timestamp=pd.Timestamp(row[experiment_data.STANDARD_TIME])
                    .tz_localize(None)
                    .to_pydatetime(),
                    curve=(),
                    lmp=float(row[LMP_COLUMN]),
                    hsl=float(row[experiment_data.EFFECTIVE_HSL]),
                    lsl=float(row[experiment_data.EFFECTIVE_LSL]),
                    state_of_charge=member.initial_soc,
                    minimum_soc=member.minimum_soc,
                    maximum_soc=member.maximum_soc,
                    source_row=int(row[experiment_data.SOURCE_ROW]),
                    status="AVAILABLE" if available else "UNAVAILABLE",
                    raw_hsl=float(row[HSL_COLUMN]),
                    raw_lsl=float(row[LSL_COLUMN]),
                    hourly_curves=row["Resolved Curves"],
                    metadata=metadata,
                )
            )
            continue
        source_rows = json.loads(row["Source Rows"])
        if len(source_rows) != 12 or len(row["Source Curves"]) != 12:
            raise ValueError(f"{member.name}: expected twelve source curves per hour")
        for index, (source, source_row) in enumerate(
            zip(row["Source Curves"], source_rows)
        ):
            source_time = pd.Timestamp(
                row[experiment_data.STANDARD_TIME]
            ) + pd.Timedelta(minutes=5 * index)
            values = {
                key: "" if pd.isna(source[key]) else str(source[key]).strip()
                for key in experiment_data.CURVE_COLUMNS
            }
            empty = not any(values.values())
            if not empty:
                try:
                    curve = _parse_curve(values, member.source_path, source_row)
                except ValueError:
                    if available:
                        raise
                    # An unusable off-hour record must not replace the cache.
                    continue
                previous = (curve, source_row, source_time)
            if not available:
                continue
            if previous is None:
                raise ValueError(
                    f"{member.source_path.name}:{source_row}: empty submitted TPO "
                    "curve has no earlier valid curve for this member"
                )
            curve, curve_row, curve_time = previous
            curves.append(curve)
            origins.append(
                {
                    "source_row": source_row,
                    "time": source_time.isoformat(),
                    "curve_source_row": curve_row,
                    "curve_time": curve_time.isoformat(),
                    "carried_forward": empty,
                    "age_minutes": int((source_time - curve_time).total_seconds() / 60),
                }
            )
        metadata = experiment_data.result_metadata(row)
        metadata.update(
            {
                "Curve Policy": CURVE_POLICY,
                "Curve Carry Forward Count": sum(o["carried_forward"] for o in origins),
                "Curve Sources": json.dumps(origins, separators=(",", ":")),
            }
        )
        events.append(
            MarketEvent(
                timestamp=row[experiment_data.STANDARD_TIME].to_pydatetime(),
                curve=(),
                lmp=float(row[LMP_COLUMN]),
                hsl=float(row[experiment_data.EFFECTIVE_HSL]),
                lsl=float(row[experiment_data.EFFECTIVE_LSL]),
                state_of_charge=member.initial_soc,
                minimum_soc=member.minimum_soc,
                maximum_soc=member.maximum_soc,
                source_row=int(row[experiment_data.SOURCE_ROW]),
                status="AVAILABLE" if row[experiment_data.AVAILABLE] else "UNAVAILABLE",
                raw_hsl=float(row[HSL_COLUMN]),
                raw_lsl=float(row[LSL_COLUMN]),
                hourly_curves=tuple(curves),
                metadata=metadata,
            )
        )
    return ResourceData(member.name, tuple(events), experimental=True)


def _compute_member(
    path: Path,
    experiment: experiment_data.Experiment,
    count: int,
    validate_only: bool,
) -> BenchmarkMemberResult:
    """Read, resolve curves, and calculate both methods in one worker."""
    member, audit = experiment_data.inspect_member(path, experiment, tail_steps=0)
    if member is None:
        return BenchmarkMemberResult(audit["member"], audit, {})
    parameters = member.frame.attrs["ess_parameters"]
    try:
        prepared = from_experiment_member(member, experiment.steps)
    except ValueError as exc:
        audit.update(valid=False, reason=str(exc))
        return BenchmarkMemberResult(member.name, audit, parameters)
    audit["carried_forward_source_curves"] = sum(
        event.metadata["Curve Carry Forward Count"] for event in prepared.events
    )
    if validate_only:
        return BenchmarkMemberResult(member.name, audit, parameters)
    resource = ResourceData(member.name, prepared.events[:count], experimental=True)
    frames = _build_output_frames(resource)
    for method, frame in zip(("Piecewise", "Discrete"), frames):
        experiment_data.validate_result_frame(
            frame, method, expected_start=experiment.start, expected_steps=count
        )
    return BenchmarkMemberResult(member.name, audit, parameters, frames)


def run_experiment(
    experiment_path: Path,
    *,
    members: list[str] | None = None,
    results_dir: Path | None = None,
    steps: int | None = None,
    validate_only: bool = False,
    workers: int = DEFAULT_WORKERS,
    month: str | None = None,
) -> BenchmarkSummary:
    """Process independent members in parallel, then export ordered results."""
    months = experiment_data.experiment_months(experiment_path, month)
    if len(months) > 1:
        results, failures = [], []
        for m in months:
            try:
                results.append(
                    run_experiment(
                        experiment_path,
                        members=members,
                        results_dir=results_dir,
                        steps=steps,
                        validate_only=validate_only,
                        workers=workers,
                        month=m,
                    )
                )
            except ValueError as exc:
                if not validate_only:
                    raise
                failures.append(f"{m}: {exc}")
                print(f"Validation failed for {m}: {exc}", flush=True)
        from Monthly_Data_1hour import write_batch_summary

        write_batch_summary(
            experiment_path,
            months,
            "benchmark",
            validate_only=validate_only,
            results_root=results_dir,
        )
        if failures:
            raise ValueError("monthly validation failures: " + "; ".join(failures))
        return BenchmarkSummary(
            tuple(p for r in results for p in r.output_paths),
            tuple(n for r in results for n in r.skipped_resources),
        )
    experiment = experiment_data.load_experiment(experiment_path, month)
    monthly = experiment.payload.get("schema_version") == 3
    if experiment.efficiency != ONE_WAY_EFFICIENCY:
        raise ValueError("Benchmark experiment requires efficiency=0.95")
    count = experiment.steps if steps is None else steps
    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or not 1 <= count <= experiment.steps
    ):
        raise ValueError(f"steps must be in 1..{experiment.steps}")
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError("workers must be a positive integer")
    names = experiment_data.selected_names(experiment, members)
    source_paths = experiment_data.discover_member_paths(experiment)
    if monthly:
        suffix = experiment.payload["month"].replace("-", "")
        for name in names:
            source_paths.setdefault(
                name, experiment.data_dir / f"{name}_Usage_{suffix}.xlsx"
            )
    missing = set(names) - source_paths.keys()
    if missing:
        raise FileNotFoundError(f"missing selected members: {sorted(missing)}")
    worker_count = min(workers, len(names))
    output = (
        results_dir / "benchmark"
        if monthly and results_dir is not None
        else results_dir or experiment.output_root / "benchmark"
    )
    run_provenance = None
    if not validate_only:
        run_provenance = experiment_data.provenance(
            experiment,
            [source_paths[name] for name in names if source_paths[name].exists()],
        )
        experiment_data.atomic_json(
            output / experiment.output_name("run_manifest.json"),
            {
                "status": "running",
                "provenance": run_provenance,
                "workers": worker_count,
            },
        )
    print(
        f"Benchmark: {len(names)} members, {worker_count} worker processes", flush=True
    )
    results = {}
    with ProcessPoolExecutor(
        max_workers=worker_count, mp_context=mp.get_context("spawn")
    ) as pool:
        futures = [
            pool.submit(
                _compute_member, source_paths[name], experiment, count, validate_only
            )
            for name in names
        ]
        for future in as_completed(futures):
            result = future.result()
            results[result.name] = result
            print(
                f"Benchmark worker {len(results)}/{len(names)}: "
                f"{result.name}: {result.audit['reason']}",
                flush=True,
            )
    reports = experiment.output_root / "reports"
    audits = [results[name].audit for name in names]
    experiment_data.atomic_json(
        reports / experiment.output_name("selected_quality_tail0_1hour.json"), audits
    )
    pd.DataFrame(audits).to_csv(
        reports / experiment.output_name("selected_quality_tail0_1hour.csv"),
        index=False,
    )
    if monthly:
        from Monthly_Data_1hour import write_quality

        write_quality(experiment, audits)
    skipped = tuple(
        name
        for name in names
        if monthly and results[name].audit.get("failure_kind") == "coverage"
    )
    invalid = [
        name
        for name in names
        if not results[name].audit["valid"] and name not in skipped
    ]
    if invalid:
        raise ValueError(
            f"selected members failed quality checks: {invalid}; see {reports}"
        )
    if validate_only:
        print("Input validation complete; no simulation performed.", flush=True)
        return BenchmarkSummary((), skipped)
    names = tuple(name for name in names if name not in skipped)
    if not names:
        raise ValueError("no valid member months remain after coverage exclusions")
    if monthly and skipped:
        run_provenance = experiment_data.provenance(
            experiment, [source_paths[n] for n in names]
        )
    summaries = []
    paths = []
    carry_records = []
    for name in names:
        frames = results[name].frames
        for cell in frames[0]["Curve Sources"]:
            for origin in json.loads(cell):
                if origin["carried_forward"]:
                    carry_records.append({RESOURCE_COLUMN: name, **origin})
        for method, frame in zip(("Piecewise", "Discrete"), frames):
            path = output / experiment.output_name(f"{name}_Profit_{method}_1hour.xlsx")
            _write_excel_atomic(frame, path)
            paths.append(path)
            summary = experiment_data.summarize_frame(frame, method)
            summary["carried_forward_source_curves"] = int(
                frame["Curve Carry Forward Count"].sum()
            )
            summary["hours_with_carried_forward_curves"] = int(
                frame["Curve Carry Forward Count"].gt(0).sum()
            )
            summaries.append(summary)
        print(f"Benchmark exported: {name} ({count} steps)", flush=True)
    pd.DataFrame(summaries).to_csv(
        output / experiment.output_name("member_summary_1hour.csv"), index=False
    )
    carry_path = output / experiment.output_name("curve_carry_forward_1hour.csv")
    pd.DataFrame(
        carry_records,
        columns=[
            RESOURCE_COLUMN,
            "source_row",
            "time",
            "curve_source_row",
            "curve_time",
            "carried_forward",
            "age_minutes",
            "source_file",
            "curve_source_file",
        ],
    ).to_csv(carry_path, index=False)
    experiment_data.atomic_json(
        output / experiment.output_name("run_manifest.json"),
        {
            "status": "complete",
            "provenance": run_provenance,
            "implementation_steps": count,
            "processed_resources": list(names),
            "skipped_coverage": list(skipped),
            "execution": "member_process_pool",
            "workers": worker_count,
            "outputs": [str(p.resolve()) for p in paths],
            "interval_minutes": 60,
            "energy_unit": "MWh",
            "curve_policy": CURVE_POLICY,
            "curve_policy_assumption": (
                "Reuse the latest earlier locally valid whole curve across hours/days; "
                "source snapshots do not establish offer expiration or withdrawal."
            ),
            "carried_forward_source_curves": len(carry_records),
            "curve_carry_forward_report": str(carry_path.resolve()),
            "ess_parameters": {name: results[name].parameters for name in names},
        },
    )
    return BenchmarkSummary(tuple(paths), skipped)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--experiment", type=Path, default=BASE_DIR / "experiment_config_1hour.json"
    )
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--member", action="append")
    parser.add_argument("--steps", type=int)
    parser.add_argument("--results-dir", type=Path)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--month", help="YYYY-MM; omit to run every configured month")
    args = parser.parse_args(argv)
    summary = run_experiment(
        args.experiment,
        members=args.member,
        results_dir=args.results_dir,
        steps=args.steps,
        validate_only=args.validate_only,
        workers=args.workers,
        month=args.month,
    )
    print(f"Workbooks written: {len(summary.output_paths)}")


if __name__ == "__main__":
    main()
