"""Read validated Micro dispatch and income for a Macro system month.

This module performs no optimization and never rewrites Micro workbooks. The
configured cohort and monthly comparison own the selection and income contract;
the Bridge workbook must match the individual Rolling dispatch contributions.
Month-end energy valuation reuses Compare_Month's existing pure helpers.
"""

from __future__ import annotations

import importlib
import hashlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd
from openpyxl import load_workbook


UTC_HOUR = "Hour Start UTC"
LOCAL_TIME = "Interval Start"
RESOURCE = "Resource Name"
INCOME_FIELDS = (
    "Actual_Realized_Profit",
    "Final_SOC_Value_Realized",
    "Sum_Realized",
    "Actual_Rolling_Window_Profit",
    "Final_SOC_Value_Rolling",
    "Sum_Rolling",
)
EXPECTED_MEMBERS = 13
NUMERIC_ATOL = 1e-6
NUMERIC_RTOL = 1e-10
ROLLING_COLUMNS = (
    "Time",
    UTC_HOUR,
    RESOURCE,
    "Month",
    "LMP",
    "Profit",
    "Cleared MW",
    "Telemetered Net Output",
    "Modeled SOC Before",
    "Modeled SOC After",
    "Applied Minimum SOC",
    "Applied Maximum SOC",
    "Effective LSL",
    "Effective HSL",
    "Capacity MWh",
    "Initial Energy MWh",
    "Normalized SOC Before",
    "Normalized SOC After",
    "Rated Charge MW",
    "Rated Discharge MW",
    "Market Participating",
    "Zero Output Day",
    "Trading Available",
    "Source Status Counts",
)


@dataclass(frozen=True)
class MicroMonth:
    """Aligned fleet MW, hourly trading dollars and existing monthly income.

    ``utc_index`` is timezone-aware UTC. Every array has one value per actual
    hour; positive power means discharge. Monthly income includes the unchanged
    per-member terminal valuation, not a single aggregate energy times Hub LMP.
    ``sources`` lists the exact input files for the caller's provenance hashing.
    """

    utc_index: pd.DatetimeIndex
    resources: tuple[str, ...]
    control_share: float
    modeled_mw: np.ndarray
    actual_mw: np.ndarray
    rolling_profit: np.ndarray
    actual_profit: np.ndarray
    monthly_income: dict[str, float]
    sources: tuple[Path, ...]
    source_hashes: dict[Path, str] = field(default_factory=dict)


class _SourceSnapshot:
    """Detect an input replaced or modified during this monthly read."""

    def __init__(self) -> None:
        self.states: dict[Path, tuple[int, int]] = {}
        self.hashes: dict[Path, str] = {}

    def track(self, path: Path) -> Path:
        path = path.resolve()
        if not path.is_file():
            raise FileNotFoundError(f"missing required Micro input: {path}")
        if path not in self.states:
            state = path.stat()
            self.states[path] = (state.st_size, state.st_mtime_ns)
            self.hashes[path] = _sha256(path)
            after_hash = path.stat()
            if (after_hash.st_size, after_hash.st_mtime_ns) != self.states[path]:
                raise ValueError(f"Micro input changed while fingerprinting: {path}")
        return path

    def verify(self) -> tuple[Path, ...]:
        for path, expected in self.states.items():
            if not path.is_file():
                raise ValueError(f"Micro input disappeared during validation: {path}")
            state = path.stat()
            if (state.st_size, state.st_mtime_ns) != expected:
                raise ValueError(f"Micro input changed during validation: {path}")
            if _sha256(path) != self.hashes[path]:
                raise ValueError(
                    f"Micro input content changed during validation: {path}"
                )
        return tuple(self.states)


def _sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _read_cells(path: Path, sheet_name: str) -> pd.DataFrame:
    """Retain Excel boolean values before validating numeric source columns.

    Even pandas' object-dtype Excel parser can turn a mixed boolean/number
    column into numeric 0/1. Read native cells first and use an object frame;
    empty cells become the same empty string used by existing Micro helpers.
    """
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        if sheet_name not in workbook.sheetnames:
            raise ValueError(f"{path}: missing worksheet {sheet_name!r}")
        rows = workbook[sheet_name].iter_rows(values_only=True)
        headers = next(rows, ())
        if not headers or any(
            not isinstance(header, str) or not header for header in headers
        ):
            raise ValueError(f"{path}: worksheet must have populated string headers")
        if len(set(headers)) != len(headers):
            raise ValueError(f"{path}: duplicate worksheet headers")
        values = [["" if value is None else value for value in row] for row in rows]
        return pd.DataFrame(values, columns=headers, dtype=object)
    finally:
        workbook.close()


def _micro_modules(project_root: Path) -> tuple[ModuleType, ModuleType]:
    micro_dir = project_root / "Program" / "Micro"
    if not micro_dir.is_dir():
        raise FileNotFoundError(f"Micro program directory does not exist: {micro_dir}")
    if str(micro_dir) not in sys.path:
        sys.path.insert(0, str(micro_dir))
    experiment_data = importlib.import_module("Experiment_Data_1hour")
    comparison = importlib.import_module("Compare_Month")
    return experiment_data, comparison


def _required_columns(
    frame: pd.DataFrame, columns: tuple[str, ...], path: Path
) -> None:
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise ValueError(f"{path}: missing required columns: {missing}")


def _numeric(frame: pd.DataFrame, column: str, path: Path, comparison) -> pd.Series:
    values = comparison._number(frame, column, path)
    if frame[column].map(lambda value: isinstance(value, (bool, np.bool_))).any():
        raise ValueError(f"{path}: column {column!r} contains boolean numbers")
    return values


def _comparison_detail(
    path: Path, resources: tuple[str, ...], months: tuple[str, ...], comparison
) -> pd.DataFrame:
    frame = _read_cells(path, "Monthly_Detail")
    _required_columns(frame, ("Resource", "Month", *INCOME_FIELDS), path)
    for key in ("Resource", "Month"):
        if (
            not frame[key]
            .map(lambda value: isinstance(value, str) and bool(value))
            .all()
        ):
            raise ValueError(f"{path}: {key} must contain nonempty string keys")
    if frame.duplicated(["Resource", "Month"]).any():
        raise ValueError(f"{path}: duplicate Resource/Month income keys")
    expected = {(resource, month) for resource in resources for month in months}
    actual = set(frame[["Resource", "Month"]].itertuples(index=False, name=None))
    if actual != expected:
        raise ValueError(
            f"{path}: Monthly_Detail must contain exactly the configured "
            f"{len(expected)} member-month keys; missing={sorted(expected - actual)}, "
            f"unexpected={sorted(actual - expected)}"
        )
    for column in INCOME_FIELDS:
        frame[column] = _numeric(frame, column, path, comparison)
    for total, trading, terminal in (
        ("Sum_Realized", "Actual_Realized_Profit", "Final_SOC_Value_Realized"),
        ("Sum_Rolling", "Actual_Rolling_Window_Profit", "Final_SOC_Value_Rolling"),
    ):
        if not np.allclose(
            frame[total],
            frame[trading] + frame[terminal],
            rtol=NUMERIC_RTOL,
            atol=NUMERIC_ATOL,
        ):
            raise ValueError(
                f"{path}: {total} does not equal trading plus terminal value"
            )
    return frame.set_index(["Resource", "Month"])


def _rolling_frame(path: Path, resource: str, experiment, experiment_data, comparison):
    frame = _read_cells(path, "Rolling")
    _required_columns(frame, ROLLING_COLUMNS, path)
    if not frame[RESOURCE].eq(resource).all():
        raise ValueError(f"{path}: Resource Name does not match {resource!r}")
    if not frame["Month"].eq(experiment.payload["month"]).all():
        raise ValueError(f"{path}: Month does not match the selected experiment")
    frame[UTC_HOUR] = comparison._utc_times(frame, UTC_HOUR, path)
    frame = frame.sort_values(UTC_HOUR, kind="stable").reset_index(drop=True)
    expected = pd.date_range(
        experiment.start.tz_convert("UTC"),
        experiment.end_exclusive.tz_convert("UTC"),
        freq="h",
        inclusive="left",
    )
    if not pd.DatetimeIndex(frame[UTC_HOUR]).equals(expected):
        raise ValueError(
            f"{path}: expected {len(expected)} unique consecutive UTC hours"
        )
    for column in ("LMP", "Profit", "Cleared MW", "Telemetered Net Output"):
        frame[column] = _numeric(frame, column, path, comparison)
    if "Power Limit Policy" in frame:
        _required_columns(
            frame, ("Submitted HSL Maximum", "Submitted LSL Minimum"), path
        )
    try:
        experiment_data.validate_result_frame(
            frame,
            "Rolling",
            expected_start=experiment.start,
            expected_steps=experiment.steps,
            efficiency=experiment.efficiency,
        )
    except (TypeError, KeyError, ValueError) as exc:
        raise ValueError(f"{path}: invalid Micro Rolling result: {exc}") from exc
    return frame


def _bridge_frame(
    path: Path,
    expected: pd.DatetimeIndex,
    resources: tuple[str, ...],
    month: str,
    member_power: dict[str, np.ndarray],
    comparison,
) -> None:
    frame = _read_cells(path, "Output")
    columns = (LOCAL_TIME, "ESR_Output_MW", UTC_HOUR, "Month", *resources)
    _required_columns(frame, columns, path)
    if set(frame.columns) != set(columns) or len(frame.columns) != len(columns):
        raise ValueError(f"{path}: Bridge must contain exactly the configured 13 ESRs")
    if not frame["Month"].eq(month).all():
        raise ValueError(f"{path}: Bridge Month does not match {month!r}")
    frame[UTC_HOUR] = comparison._utc_times(frame, UTC_HOUR, path)
    frame = frame.sort_values(UTC_HOUR, kind="stable").reset_index(drop=True)
    if not pd.DatetimeIndex(frame[UTC_HOUR]).equals(expected):
        raise ValueError(
            f"{path}: Bridge UTC hours do not match the complete local month"
        )
    try:
        local = pd.DatetimeIndex(pd.to_datetime(frame[LOCAL_TIME], errors="raise"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{path}: invalid Bridge local timestamps") from exc
    if not local.equals(expected.tz_convert("America/Chicago").tz_localize(None)):
        raise ValueError(f"{path}: Bridge local timestamps disagree with UTC")
    for resource in resources:
        power = _numeric(frame, resource, path, comparison).to_numpy(dtype=float)
        if not np.allclose(
            power, member_power[resource], rtol=NUMERIC_RTOL, atol=NUMERIC_ATOL
        ):
            raise ValueError(f"{path}: Bridge {resource} does not match Micro Rolling")
    total = _numeric(frame, "ESR_Output_MW", path, comparison).to_numpy(dtype=float)
    expected_total = np.sum([member_power[resource] for resource in resources], axis=0)
    if not np.allclose(total, expected_total, rtol=NUMERIC_RTOL, atol=NUMERIC_ATOL):
        raise ValueError(
            f"{path}: Bridge ESR_Output_MW does not equal member dispatch sum"
        )


def load_micro_month(
    project_root: Path, month: str, experiment_path: Path | None = None
) -> MicroMonth:
    """Load one configured YYYY-MM without changing any existing Micro result.

    Reject incomplete grids, invalid physical Rolling results, stale Bridge or
    income values, and files changed while reading. Terminal values retain the
    Compare_Month algorithm. Monthly income is returned from the validated
    authoritative comparison sheet; hourly values retain original precision.
    """
    project_root = Path(project_root).resolve()
    experiment_data, comparison = _micro_modules(project_root)
    snapshot = _SourceSnapshot()
    config_path = snapshot.track(
        Path(experiment_path)
        if experiment_path is not None
        else project_root / "Program" / "Micro" / "experiment_config_1hour.json"
    )
    experiment = experiment_data.load_experiment(config_path, month)
    if experiment.payload.get("schema_version") != 3:
        raise ValueError(
            "Macro system postprocessing requires a monthly schema_version=3"
        )
    resources = experiment_data.selected_names(experiment, None)
    if len(resources) != EXPECTED_MEMBERS:
        raise ValueError(f"Macro system requires 13 ESRs; found {len(resources)}")
    months = experiment_data.experiment_months(config_path)
    expected = pd.date_range(
        experiment.start.tz_convert("UTC"),
        experiment.end_exclusive.tz_convert("UTC"),
        freq="h",
        inclusive="left",
    )
    comparison_path = snapshot.track(experiment.output_root / "Comparison_Month.xlsx")
    detail = _comparison_detail(comparison_path, resources, months, comparison)
    token = month.replace("-", "")
    member_power: dict[str, np.ndarray] = {}
    actual_power: list[np.ndarray] = []
    rolling_profit: list[np.ndarray] = []
    actual_profit: list[np.ndarray] = []

    for resource in resources:
        rolling_path = snapshot.track(
            experiment.output_root
            / "rolling"
            / f"{resource}_Rolling_Window_1hour_{token}.xlsx"
        )
        realized_path = snapshot.track(
            experiment.output_root
            / "realized_profit"
            / f"Realized_Profit_{resource}_1hour_{token}.xlsx"
        )
        snapshot.track(experiment.data_dir / f"{resource}_Usage_{token}.xlsx")
        rolling = _rolling_frame(
            rolling_path, resource, experiment, experiment_data, comparison
        )
        realized = comparison._read_hourly(
            realized_path, resource, experiment, rolling=False
        )
        terminal_rolling = comparison._final_soc_value(
            rolling, rolling_path, "Modeled SOC After"
        )
        terminal_actual = comparison._realized_soc_value(
            realized, realized_path, experiment.data_dir, resource, experiment
        )
        trading_rolling = float(rolling["Profit"].sum())
        trading_actual = float(realized["Profit"].sum())
        recomputed = np.array(
            [
                trading_actual,
                terminal_actual,
                trading_actual + terminal_actual,
                trading_rolling,
                terminal_rolling,
                trading_rolling + terminal_rolling,
            ]
        )
        stored = detail.loc[(resource, month), list(INCOME_FIELDS)].to_numpy(
            dtype=float
        )
        if not np.allclose(recomputed, stored, rtol=NUMERIC_RTOL, atol=NUMERIC_ATOL):
            raise ValueError(
                f"{comparison_path}: stale income for {resource}, {month}; "
                "monthly fields do not match current Micro inputs"
            )
        member_power[resource] = rolling["Cleared MW"].to_numpy(dtype=float)
        actual_power.append(rolling["Telemetered Net Output"].to_numpy(dtype=float))
        rolling_profit.append(rolling["Profit"].to_numpy(dtype=float))
        actual_profit.append(realized["Profit"].to_numpy(dtype=float))

    bridge_path = snapshot.track(
        project_root
        / "Results"
        / "Macro_Results"
        / "ESR_Output_Rolling"
        / f"ESR_Rolling_Output_{token}.xlsx"
    )
    _bridge_frame(bridge_path, expected, resources, month, member_power, comparison)
    monthly_income = {
        field: float(detail.xs(month, level="Month")[field].sum(skipna=False))
        for field in INCOME_FIELDS
    }
    arrays = [
        np.sum([member_power[resource] for resource in resources], axis=0),
        np.sum(actual_power, axis=0),
        np.sum(rolling_profit, axis=0),
        np.sum(actual_profit, axis=0),
    ]
    if not all(np.isfinite(values).all() for values in arrays) or not all(
        np.isfinite(value) for value in monthly_income.values()
    ):
        raise ValueError(f"{month}: non-finite aggregate Micro contribution")
    return MicroMonth(
        utc_index=expected,
        resources=resources,
        control_share=float(experiment.payload["control_share"]),
        modeled_mw=arrays[0],
        actual_mw=arrays[1],
        rolling_profit=arrays[2],
        actual_profit=arrays[3],
        monthly_income=monthly_income,
        sources=snapshot.verify(),
        source_hashes=dict(snapshot.hashes),
    )
