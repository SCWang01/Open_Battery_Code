"""Hourly rolling bidding with monthly submitted power and SOC bounds.

Default input is experiment_config_1hour.json; results live directly in Results/Micro_Results/rolling with YYYYMM filenames.
The CLI runs all configured months using 12 processes; --month selects one.
The hourly loader uses raw monthly max(HSL)/min(LSL) for power ratings.
SOC bounds are raw monthly Minimum/Maximum SOC extrema. Each month starts at
its first observed State of Charge; forecast-tail rows do not set these values.
Any ON/ONTEST source slot enables the hour unless its entire day has zero hourly
net output. Every forecast hour carries its own gate. There is no terminal SOC
target; reported Modeled SOC and dsfunction column eight are energy in MWh.

"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import tempfile
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Sequence

import gurobipy as gp
import numpy as np
import pandas as pd
from gurobipy import GRB
from openpyxl.styles import Alignment
from openpyxl.utils import get_column_letter

import Experiment_Data_1hour as experiment_data


# ---------------------------------------------------------------------------
# Contract and configuration
# ---------------------------------------------------------------------------

from project_paths import MICRO_DATA_DIR, MICRO_RESULTS_DIR

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = MICRO_DATA_DIR / "Six_Month_Data"
DEFAULT_RESULTS_DIR = MICRO_RESULTS_DIR / "rolling"
DEFAULT_EXPERIMENT_PATH = BASE_DIR / "experiment_config_1hour.json"

DEFAULT_HORIZON_STEPS = 24
MAX_HORIZON_STEPS = 36
INNOVATION_DAY_CAPACITY = 31

# Calendar defaults are resolved from the experiment configuration before use.
STUDY_START_DATE = date(2026, 6, 1)
STUDY_END_DATE = date(2026, 6, 30)
LAST_DAY_STEPS: int | None = None

TIME_COLUMN = "SCED Time Stamp"
RESOURCE_COLUMN = "Resource Name"
LMP_COLUMN = "LMP"
HSL_COLUMN = "HSL"
LSL_COLUMN = "LSL"
TELEMETRY_COLUMN = "Telemetered Net Output"
OBSERVED_SOC_COLUMN = "State of Charge"
MINIMUM_SOC_COLUMN = "Minimum SOC"
MAXIMUM_SOC_COLUMN = "Maximum SOC"
INPUT_PATTERN = "*_Usage.csv"
TIME_FORMAT = "%m/%d/%Y %H:%M:%S"
AVAILABLE_STATUSES = ("ON", "ONTEST")
POWER_LIMIT_POLICY = (
    experiment_data.SUBMITTED_POWER_POLICY + "_hourly_any_ON_ONTEST_nonzero_day"
)
SOC_POLICY = experiment_data.SUBMITTED_SOC_POLICY

REQUIRED_COLUMNS = (
    TIME_COLUMN,
    RESOURCE_COLUMN,
    LMP_COLUMN,
    HSL_COLUMN,
    LSL_COLUMN,
    OBSERVED_SOC_COLUMN,
    MINIMUM_SOC_COLUMN,
    MAXIMUM_SOC_COLUMN,
    TELEMETRY_COLUMN,
)
NUMERIC_INPUT_COLUMNS = (
    LMP_COLUMN,
    HSL_COLUMN,
    LSL_COLUMN,
    OBSERVED_SOC_COLUMN,
    MINIMUM_SOC_COLUMN,
    MAXIMUM_SOC_COLUMN,
)

OUTPUT_COLUMNS = (
    "Time",
    RESOURCE_COLUMN,
    "Price Source",
    LMP_COLUMN,
    HSL_COLUMN,
    LSL_COLUMN,
    "Pdmax",
    "Pcmax",
    "Observed SOC",
    "Modeled SOC Before",
    "Applied Minimum SOC",
    "Applied Maximum SOC",
    "Cleared MW",
    "Modeled SOC After",
    "Profit",
    "Power Limit Reached",
    "SOC Boundary Reached",
    "Cleared SOC Limit State",
    "dsfunction",
)
EXCEL_COLUMN_WIDTHS = {
    "Time": 21,
    RESOURCE_COLUMN: 27,
    "Price Source": 18,
    LMP_COLUMN: 14,
    HSL_COLUMN: 14,
    LSL_COLUMN: 14,
    "Pdmax": 14,
    "Pcmax": 14,
    "Observed SOC": 18,
    "Modeled SOC Before": 22,
    "Applied Minimum SOC": 22,
    "Applied Maximum SOC": 22,
    "Cleared MW": 17,
    "Modeled SOC After": 22,
    "Profit": 18,
    "Power Limit Reached": 21,
    "SOC Boundary Reached": 22,
    "Cleared SOC Limit State": 25,
    "dsfunction": 100,
}


@dataclass(frozen=True)
class RollingConfig:
    """Editable experiment settings and their validated invariants."""

    data_dir: Path = DEFAULT_DATA_DIR
    results_dir: Path = DEFAULT_RESULTS_DIR
    implementation_steps: int | None = None
    horizon_steps: int = DEFAULT_HORIZON_STEPS
    study_start_date: date = STUDY_START_DATE
    study_end_date: date = STUDY_END_DATE
    last_day_steps: int | None = LAST_DAY_STEPS
    price_samples: int = 300
    expected_standard_points: int = 24
    intervals_per_hour: float = 1.0
    efficiency: float = 0.95
    seed: int = 42
    meanstd_percent: float = 2.0
    forecast_error_increment: float = 0.001
    power_tolerance: float = 1e-3
    # MWh boundary-classification band; never used to snap physical energy.
    soc_tolerance: float = 1e-3
    experiment_path: Path | None = DEFAULT_EXPERIMENT_PATH
    solver_threads: int = 1
    month: str | None = None
    physical_month_steps: int | None = None

    def output_name(self, filename: str) -> str:
        return experiment_data.monthly_output_name(filename, self.month)

    @property
    def interval_hours(self) -> float:
        return 1.0 / self.intervals_per_hour

    @property
    def study_day_count(self) -> int:
        return (self.study_end_date - self.study_start_date).days + 1

    @property
    def planned_implementation_steps(self) -> int:
        if self.physical_month_steps is not None:
            return self.physical_month_steps
        if self.last_day_steps is None:
            raise ValueError("config must be validated before deriving study length")
        return (
            self.expected_standard_points * (self.study_day_count - 1)
            + self.last_day_steps
        )

    @property
    def required_study_points(self) -> int:
        return self.planned_implementation_steps + self.horizon_steps - 1


@dataclass(frozen=True)
class ResourceData:
    """Validated grid with study-range Pcmax/Pdmax attached by load_resources."""

    name: str
    source_path: Path
    frame: pd.DataFrame
    minimum_soc: float
    maximum_soc: float
    initial_soc: float
    price_min: float | None = None
    price_max: float | None = None


@dataclass(frozen=True)
class BidResult:
    """A V5-style compressed bidding function for one implementation step."""

    dsfunction: np.ndarray


@dataclass(frozen=True)
class DemoResult:
    """Files and resources produced by one successful demo run."""

    detail_paths: tuple[Path, ...]
    summary_path: Path
    manifest_path: Path
    random_path: Path | None
    skipped_resources: tuple[str, ...]
    random_records: tuple[experiment_data.RandomRecord, ...] = ()


def validate_config(config: RollingConfig) -> RollingConfig:
    """Validate external configuration before any input or solver work."""

    if config.experiment_path is None:
        raise ValueError("hourly Rolling requires an experiment configuration")
    if config.experiment_path is not None:
        experiment = experiment_data.load_experiment(
            config.experiment_path, config.month
        )
        if experiment.payload["soc_policy"] != SOC_POLICY:
            raise ValueError(f"Rolling requires soc_policy={SOC_POLICY!r}")
        config = replace(
            config,
            data_dir=experiment.data_dir,
            month=experiment.payload.get("month"),
            results_dir=(
                (experiment.output_root / "rolling")
                if config.results_dir == DEFAULT_RESULTS_DIR
                else config.results_dir
            ),
            study_start_date=experiment.start.date(),
            study_end_date=(experiment.end_exclusive - pd.Timedelta(hours=1)).date(),
            physical_month_steps=experiment.steps,
            last_day_steps=24,
            efficiency=experiment.efficiency,
            meanstd_percent=experiment.meanstd_percent,
            forecast_error_increment=experiment.forecast_error_increment,
        )
        if config.solver_threads != 1:
            raise ValueError("experiment requires --solver-threads 1")
        if experiment.payload.get("schema_version") == 3 and config.horizon_steps > 25:
            raise ValueError(
                "monthly files allow horizons up to 25 hours (24 tail hours)"
            )
        if config.expected_standard_points != 24 or config.intervals_per_hour != 1:
            raise ValueError("hourly experiment requires 24 one-hour points per day")
    if not config.data_dir.is_dir():
        raise FileNotFoundError(f"data directory does not exist: {config.data_dir}")
    for name, value in {
        "study_start_date": config.study_start_date,
        "study_end_date": config.study_end_date,
    }.items():
        if isinstance(value, datetime) or not isinstance(value, date):
            raise ValueError(f"{name} must be a datetime.date, found {value!r}")
    if config.study_start_date > config.study_end_date:
        raise ValueError(
            "study_start_date must not follow study_end_date "
            f"({config.study_start_date} > {config.study_end_date})"
        )
    study_day_count = (config.study_end_date - config.study_start_date).days + 1
    if study_day_count > INNOVATION_DAY_CAPACITY:
        raise ValueError(
            f"study period has {study_day_count} days; maximum is "
            f"{INNOVATION_DAY_CAPACITY}"
        )
    integer_fields = {
        "horizon_steps": config.horizon_steps,
        "price_samples": config.price_samples,
        "expected_standard_points": config.expected_standard_points,
    }
    for name, value in integer_fields.items():
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer, found {value!r}")
    if config.price_samples < 2:
        raise ValueError("price_samples must be at least 2")
    if config.horizon_steps > MAX_HORIZON_STEPS:
        raise ValueError(
            f"horizon_steps must not exceed {MAX_HORIZON_STEPS}, "
            f"found {config.horizon_steps}"
        )
    maximum_last_day_steps = config.expected_standard_points - (
        0 if config.experiment_path is not None else config.horizon_steps
    )
    if maximum_last_day_steps < 1:
        raise ValueError(
            "horizon_steps must leave at least one implementation point "
            f"({config.horizon_steps} >= {config.expected_standard_points})"
        )
    last_day_steps = config.last_day_steps
    if last_day_steps is None:
        last_day_steps = maximum_last_day_steps
    elif (
        isinstance(last_day_steps, bool)
        or not isinstance(last_day_steps, int)
        or last_day_steps < 1
    ):
        raise ValueError(
            "last_day_steps must be None or a positive integer, "
            f"found {last_day_steps!r}"
        )
    if last_day_steps > maximum_last_day_steps:
        raise ValueError(
            "last_day_steps must not exceed expected_standard_points - "
            f"horizon_steps ({last_day_steps} > {maximum_last_day_steps})"
        )
    planned_steps = config.planned_implementation_steps
    implementation_steps = config.implementation_steps
    if implementation_steps is None:
        implementation_steps = planned_steps
    elif (
        isinstance(implementation_steps, bool)
        or not isinstance(implementation_steps, int)
        or implementation_steps < 1
    ):
        raise ValueError(
            "implementation_steps must be None or a positive integer, "
            f"found {implementation_steps!r}"
        )
    if implementation_steps > planned_steps:
        raise ValueError(
            "implementation_steps must not exceed the configured study length "
            f"({implementation_steps} > {planned_steps})"
        )

    finite_fields = {
        "intervals_per_hour": config.intervals_per_hour,
        "efficiency": config.efficiency,
        "meanstd_percent": config.meanstd_percent,
        "forecast_error_increment": config.forecast_error_increment,
        "power_tolerance": config.power_tolerance,
        "soc_tolerance": config.soc_tolerance,
    }
    for name, value in finite_fields.items():
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite, found {value!r}")
    if config.intervals_per_hour <= 0:
        raise ValueError("intervals_per_hour must be positive")
    if not 0 < config.efficiency <= 1:
        raise ValueError("efficiency must lie in (0, 1]")
    if config.meanstd_percent < 0 or config.forecast_error_increment < 0:
        raise ValueError("forecast-error rates must be non-negative")
    if config.power_tolerance <= 0 or config.soc_tolerance <= 0:
        raise ValueError("numeric tolerances must be positive")
    return replace(
        config,
        implementation_steps=implementation_steps,
        last_day_steps=last_day_steps,
    )


# ---------------------------------------------------------------------------
# Input and persisted-random validation
# ---------------------------------------------------------------------------


def _normalized_status(frame: pd.DataFrame) -> pd.Series:
    """Normalize for decisions without replacing the published status column."""
    if experiment_data.STATUS in frame.columns:
        return (
            frame[experiment_data.STATUS].fillna("").astype(str).str.strip().str.upper()
        )
    return pd.Series("ON", index=frame.index)


def _available_rows(frame: pd.DataFrame) -> pd.Series:
    """The common loader owns the hourly participation and day-zero gate."""
    return frame[experiment_data.AVAILABLE].astype(bool)


def _load_experiment_resources(
    config: RollingConfig,
    requested_members: Sequence[str] | None,
    *,
    audit_all: bool = False,
    workers: int = 12,
):
    experiment = experiment_data.load_experiment(config.experiment_path, config.month)
    members = experiment_data.load_members(
        experiment,
        requested_members,
        tail_steps=config.horizon_steps - 1,
        audit_all=audit_all,
        workers=workers,
        power_policy=experiment_data.SUBMITTED_POWER_POLICY,
        soc_policy=SOC_POLICY,
    )
    resources = []
    for member in members:
        frame = member.frame.copy()
        frame["Original SCED Time Stamp"] = frame[experiment_data.SCED_TIME]
        frame[TIME_COLUMN] = frame[experiment_data.STANDARD_TIME]
        frame["Raw HSL"] = frame[HSL_COLUMN]
        frame["Raw LSL"] = frame[LSL_COLUMN]
        frame["Pdmax"] = frame[experiment_data.EFFECTIVE_HSL]
        frame["Pcmax"] = frame[experiment_data.EFFECTIVE_LSL]
        frame.attrs["input_power_limits"] = member.frame.attrs["ess_parameters"]
        resources.append(
            ResourceData(
                member.name,
                member.source_path,
                frame,
                member.minimum_soc,
                member.maximum_soc,
                member.initial_soc,
                member.price_min,
                member.price_max,
            )
        )
    skipped = tuple(
        n
        for n in experiment_data.selected_names(experiment, requested_members)
        if n not in {r.name for r in resources}
    )
    return tuple(resources), skipped, experiment.origin


def load_resources(
    config: RollingConfig,
    requested_members: Sequence[str] | None = None,
    *,
    workers: int = 12,
):
    return _load_experiment_resources(config, requested_members, workers=workers)


def _atomic_json_dump(payload: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            suffix=".json.tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


# ---------------------------------------------------------------------------
# Price forecasts, optimization, and clearing
# ---------------------------------------------------------------------------


def build_estimated_price_horizon(
    observed_price: np.ndarray,
    innovations: np.ndarray,
    *,
    meanstd_percent: float,
    error_increment: float,
    price_min: float,
    price_max: float,
) -> np.ndarray:
    """Apply V5 forecast errors while leaving the current LMP exact."""

    prices = np.asarray(observed_price, dtype=float)
    z = np.asarray(innovations, dtype=float)
    if prices.ndim != 1 or prices.shape != z.shape or prices.size == 0:
        raise ValueError(
            "observed_price and innovations must have the same non-empty "
            "one-dimensional shape"
        )
    if not np.isfinite(prices).all() or not np.isfinite(z).all():
        raise ValueError("price horizon and innovations must be finite")
    if not math.isfinite(price_min) or not math.isfinite(price_max):
        raise ValueError("price clipping bounds must be finite")
    if price_min > price_max:
        raise ValueError("price_min must not exceed price_max")

    estimated = prices.copy()
    future_indices = np.arange(1, prices.size)
    if future_indices.size:
        rates = meanstd_percent / 100.0 + (future_indices - 1) * error_increment
        estimated[future_indices] = np.clip(
            prices[future_indices] * (1.0 + z[future_indices] * rates),
            price_min,
            price_max,
        )
    return estimated


def _clip_soc_roundoff(
    energy: float, minimum_soc: float, maximum_soc: float, *, scale: float = 1.0
) -> float:
    """Clip only floating-point overshoot; retain every interior energy value."""
    allowance = 128 * np.finfo(float).eps * max(
        1.0, abs(energy), abs(minimum_soc), abs(maximum_soc), abs(scale)
    )
    if not math.isfinite(energy) or not (
        minimum_soc - allowance <= energy <= maximum_soc + allowance
    ):
        raise ValueError(
            f"energy {energy} exceeds [{minimum_soc}, {maximum_soc}] "
            f"beyond floating-point allowance {allowance}"
        )
    return float(np.clip(energy, minimum_soc, maximum_soc))


def _canonical_dispatch_trajectory(
    *,
    raw_charge: np.ndarray,
    raw_discharge: np.ndarray,
    charge_limits: np.ndarray,
    discharge_limits: np.ndarray,
    initial_soc: float,
    minimum_soc: float,
    maximum_soc: float,
    config: RollingConfig,
) -> tuple[np.ndarray, np.ndarray]:
    """Derive one feasible net-power/MWh trajectory for dispatch and metadata.

    Solver integrality tolerance can leave a tiny flow in the inactive direction.
    Preserve its contribution to net power, then project only numerical excess
    onto the sequential power/energy feasible interval. The power tolerance bounds
    that correction; it never deletes an otherwise feasible small action.
    """
    arrays = tuple(
        np.asarray(values, dtype=float)
        for values in (raw_charge, raw_discharge, charge_limits, discharge_limits)
    )
    pc, pd, pcmax, pdmax = arrays
    if pc.ndim != 1 or not pc.size or any(a.shape != pc.shape for a in arrays):
        raise ValueError("dispatch powers and limits must be equal nonempty vectors")
    if not all(np.isfinite(a).all() for a in arrays):
        raise ValueError("dispatch powers and limits must be finite")
    if np.any(pcmax < 0) or np.any(pdmax < 0):
        raise ValueError("dispatch power limits must be nonnegative")
    if np.any(pc < -config.power_tolerance) or np.any(pd < -config.power_tolerance):
        raise ValueError("negative solver charge/discharge exceeds power tolerance")
    if np.any(np.minimum(pc, pd) > config.power_tolerance):
        raise ValueError("simultaneous charge/discharge exceeds power tolerance")
    if not (
        math.isfinite(minimum_soc)
        and math.isfinite(maximum_soc)
        and minimum_soc <= maximum_soc
    ):
        raise ValueError("invalid energy bounds for dispatch trajectory")

    net = np.empty_like(pc)
    energy = np.empty_like(pc)
    before = _clip_soc_roundoff(initial_soc, minimum_soc, maximum_soc)
    for step, raw_net in enumerate(pd - pc):
        lower = max(
            -pcmax[step],
            -(maximum_soc - before) / config.efficiency / config.interval_hours,
        )
        upper = min(
            pdmax[step],
            (before - minimum_soc) * config.efficiency / config.interval_hours,
        )
        power = float(np.clip(raw_net, lower, upper))
        if abs(power - raw_net) > config.power_tolerance:
            raise ValueError(
                f"step {step}: feasible power projection changes {raw_net} to "
                f"{power}, exceeding tolerance {config.power_tolerance}"
            )
        after = _soc_after_power(before, power, config)
        net[step] = power
        energy[step] = _clip_soc_roundoff(
            after, minimum_soc, maximum_soc, scale=max(abs(before), abs(after - before))
        )
        before = energy[step]
    return net, energy


def _build_dsfunction(
    candidate_prices: np.ndarray,
    current_power: np.ndarray,
    ncd: np.ndarray,
) -> np.ndarray:
    """Group nearby powers while retaining one coherent exact candidate per stair."""

    rounded_power = np.round(current_power, 4)
    sign_state = np.sign(ncd[0] - ncd[1]).astype(int)
    soc_limit_state = ncd[3].astype(int)

    segment_bounds: list[tuple[int, int]] = []
    segment_start = 0
    for index in range(1, len(rounded_power) + 1):
        finished = (
            index == len(rounded_power)
            or rounded_power[index] != rounded_power[segment_start]
            or sign_state[index] != sign_state[segment_start]
            or soc_limit_state[index] != soc_limit_state[segment_start]
        )
        if finished:
            segment_bounds.append((segment_start, index))
            segment_start = index

    dsfunction = np.zeros((len(segment_bounds), 7), dtype=float)
    previous_high = candidate_prices[0]
    for stair_index, (start, end) in enumerate(segment_bounds):
        vectors = ncd[:, start:end].T
        unique, first_indices, counts = np.unique(
            vectors,
            axis=0,
            return_index=True,
            return_counts=True,
        )
        mode_candidates = np.flatnonzero(counts == counts.max())
        mode_index = mode_candidates[np.argmin(first_indices[mode_candidates])]
        dsfunction[stair_index, 0] = previous_high
        dsfunction[stair_index, 1] = candidate_prices[end - 1]
        representative = start + int(first_indices[mode_index])
        dsfunction[stair_index, 2] = current_power[representative]
        dsfunction[stair_index, 3:7] = unique[mode_index]
        previous_high = candidate_prices[end - 1]
    return dsfunction


def solve_bidding_function(
    *,
    resource: str,
    timestamp: pd.Timestamp,
    estimated_prices: np.ndarray,
    discharge_limits: np.ndarray,
    charge_limits: np.ndarray,
    initial_soc: float,
    minimum_soc: float,
    maximum_soc: float,
    config: RollingConfig,
    solver_environment: gp.Env | None = None,
) -> BidResult:
    """Solve one full fixed-length look-ahead horizon."""

    prices = np.asarray(estimated_prices, dtype=float)
    pdmax = np.asarray(discharge_limits, dtype=float)
    pcmax = np.asarray(charge_limits, dtype=float)
    horizon = prices.size
    if horizon != config.horizon_steps:
        raise ValueError(
            f"{resource} at {timestamp}: horizon length {horizon} does not equal "
            f"configured horizon_steps {config.horizon_steps}"
        )
    expected_shape = (horizon,)
    arrays = {
        "estimated_prices": prices,
        "discharge_limits": pdmax,
        "charge_limits": pcmax,
    }
    for name, array in arrays.items():
        if array.shape != expected_shape:
            raise ValueError(
                f"{resource} at {timestamp}: {name} has shape {array.shape}, "
                f"expected {expected_shape}"
            )
        if not np.isfinite(array).all():
            raise ValueError(f"{resource} at {timestamp}: {name} is non-finite")
    if (pdmax < 0).any() or (pcmax < 0).any():
        raise ValueError(
            f"{resource} at {timestamp}: power limits must be non-negative"
        )
    if not minimum_soc <= initial_soc <= maximum_soc:
        raise ValueError(
            f"{resource} at {timestamp}: initial SOC {initial_soc} is outside "
            f"[{minimum_soc}, {maximum_soc}]"
        )
    price_min = float(prices.min())
    price_max = float(prices.max())
    price_span = max(price_max - price_min, 1.0)
    candidate_prices = np.linspace(
        price_min - price_span,
        price_max + price_span,
        config.price_samples,
    )

    current_power = np.zeros(config.price_samples, dtype=float)
    ncd = np.zeros((4, config.price_samples), dtype=float)
    energy = np.zeros((horizon, config.price_samples), dtype=float)
    charge_power = np.zeros((horizon, config.price_samples), dtype=float)
    discharge_power = np.zeros((horizon, config.price_samples), dtype=float)

    if solver_environment is None:
        model = gp.Model(f"rolling_{_safe_name(resource)}")
    else:
        model = gp.Model(
            f"rolling_{_safe_name(resource)}",
            env=solver_environment,
        )
    model.Params.OutputFlag = 0
    model.Params.Seed = 0
    model.Params.Threads = config.solver_threads
    soc = model.addVars(
        horizon + 1,
        vtype=GRB.CONTINUOUS,
        lb=minimum_soc,
        ub=maximum_soc,
        name="SOC",
    )
    charge = model.addVars(horizon, vtype=GRB.CONTINUOUS, lb=0.0, name="Pc")
    discharge = model.addVars(horizon, vtype=GRB.CONTINUOUS, lb=0.0, name="Pd")
    charging = model.addVars(horizon, vtype=GRB.BINARY, name="sc")
    discharging = model.addVars(horizon, vtype=GRB.BINARY, name="sd")

    model.addConstr(soc[0] == initial_soc, "SOC_initial")
    for step in range(horizon):
        charge[step].UB = float(pcmax[step])
        discharge[step].UB = float(pdmax[step])
        model.addConstr(
            soc[step + 1]
            == soc[step]
            - (discharge[step] / config.efficiency - config.efficiency * charge[step])
            * config.interval_hours,
            f"SOC_{step}",
        )
        model.addConstr(
            charge[step] <= float(pcmax[step]) * charging[step],
            f"charge_state_{step}",
        )
        model.addConstr(
            discharge[step] <= float(pdmax[step]) * discharging[step],
            f"discharge_state_{step}",
        )
        model.addConstr(
            charging[step] + discharging[step] <= 1,
            f"exclusive_state_{step}",
        )

    future_objective = gp.quicksum(
        prices[step] * (discharge[step] - charge[step]) * config.interval_hours
        for step in range(1, horizon)
    )
    try:
        for candidate_index, candidate_price in enumerate(candidate_prices):
            model.setObjective(
                candidate_price * (discharge[0] - charge[0]) * config.interval_hours
                + future_objective,
                GRB.MAXIMIZE,
            )
            model.optimize()
            if model.Status != GRB.OPTIMAL:
                raise RuntimeError(
                    f"{resource} at {timestamp}: candidate {candidate_index} "
                    f"price {candidate_price} ended with Gurobi status "
                    f"{model.Status}"
                )

            net_power, candidate_energy = _canonical_dispatch_trajectory(
                raw_charge=np.array([charge[step].X for step in range(horizon)]),
                raw_discharge=np.array([discharge[step].X for step in range(horizon)]),
                charge_limits=pcmax,
                discharge_limits=pdmax,
                initial_soc=initial_soc,
                minimum_soc=minimum_soc,
                maximum_soc=maximum_soc,
                config=config,
            )
            discharge_power[:, candidate_index] = np.maximum(net_power, 0.0)
            charge_power[:, candidate_index] = np.minimum(net_power, 0.0)
            energy[:, candidate_index] = candidate_energy
            current_power[candidate_index] = net_power[0]

            upper_hits = np.flatnonzero(
                (energy[:, candidate_index] >= maximum_soc - config.soc_tolerance)
                & ((charge_power[:, candidate_index] + discharge_power[:, candidate_index])
                   < -config.power_tolerance)
            )
            lower_hits = np.flatnonzero(
                (energy[:, candidate_index] <= minimum_soc + config.soc_tolerance)
                & ((charge_power[:, candidate_index] + discharge_power[:, candidate_index])
                   > config.power_tolerance)
            )
            # A final-step hit is real; never-hit must sort after the window.
            first_upper = int(upper_hits[0] + 1) if len(upper_hits) else horizon + 1
            first_lower = int(lower_hits[0] + 1) if len(lower_hits) else horizon + 1
            boundary_step = min(first_upper, first_lower, horizon)
            active_slice = slice(0, boundary_step)

            charging_at_max = np.isclose(
                charge_power[active_slice, candidate_index],
                -pcmax[active_slice],
                rtol=1e-9,
                atol=config.power_tolerance,
            ) & (pcmax[active_slice] > config.power_tolerance)
            discharging_at_max = np.isclose(
                discharge_power[active_slice, candidate_index],
                pdmax[active_slice],
                rtol=1e-9,
                atol=config.power_tolerance,
            ) & (pdmax[active_slice] > config.power_tolerance)
            net_power = (
                charge_power[active_slice, candidate_index]
                + discharge_power[active_slice, candidate_index]
            )
            ncd[0, candidate_index] = np.count_nonzero(charging_at_max)
            ncd[1, candidate_index] = np.count_nonzero(discharging_at_max)
            ncd[2, candidate_index] = np.count_nonzero(
                np.abs(net_power) <= config.power_tolerance
            )
            if first_upper < first_lower:
                ncd[3, candidate_index] = 1
            elif first_lower < first_upper:
                ncd[3, candidate_index] = -1
    finally:
        model.dispose()

    return BidResult(dsfunction=_build_dsfunction(candidate_prices, current_power, ncd))


def _soc_after_power(
    soc_before: float, cleared_power: float, config: RollingConfig
) -> float:
    if cleared_power >= 0.0:
        return soc_before - (cleared_power / config.efficiency * config.interval_hours)
    return soc_before - (cleared_power * config.efficiency * config.interval_hours)


def _clear_dsfunction(dsfunction: np.ndarray, price: float) -> tuple[int, float]:
    """Clear a staircase with the confirmed later-stair boundary convention."""

    if dsfunction.ndim != 2 or dsfunction.shape[1] < 3 or len(dsfunction) == 0:
        raise ValueError("dsfunction must be a non-empty two-dimensional staircase")
    if price < dsfunction[0, 0]:
        index = 0
    elif price > dsfunction[-1, 1]:
        index = len(dsfunction) - 1
    else:
        index = int(np.searchsorted(dsfunction[:, 1], price, side="right"))
        index = min(index, len(dsfunction) - 1)
    return index, float(dsfunction[index, 2])


def run_resource(
    resource: ResourceData,
    innovations: np.ndarray,
    config: RollingConfig,
    *,
    innovation_origin_date: pd.Timestamp | None = None,
    solver_threads: int | None = None,
) -> pd.DataFrame:
    """Run one resource in a quiet, resource-scoped Gurobi environment."""

    if solver_threads is not None and (
        isinstance(solver_threads, bool) or solver_threads < 1
    ):
        raise ValueError("solver_threads must be at least 1")
    environment = gp.Env(empty=True)
    try:
        environment.setParam("OutputFlag", 0)
        environment.setParam("Seed", 0)
        if solver_threads is not None:
            environment.setParam("Threads", solver_threads)
        environment.start()
        return _run_resource_with_environment(
            resource,
            innovations,
            config,
            innovation_origin_date=innovation_origin_date,
            solver_environment=environment,
        )
    finally:
        environment.dispose()


def _run_resource_with_environment(
    resource: ResourceData,
    innovations: np.ndarray,
    config: RollingConfig,
    *,
    innovation_origin_date: pd.Timestamp | None,
    solver_environment: gp.Env,
) -> pd.DataFrame:
    """Build and clear every requested bidding function for one resource."""

    if config.implementation_steps is None:
        raise ValueError("config must be validated before running a resource")
    frame = resource.frame
    prices = frame[LMP_COLUMN].to_numpy(dtype=float)
    hsl = frame[HSL_COLUMN].to_numpy(dtype=float)
    lsl = frame[LSL_COLUMN].to_numpy(dtype=float)
    if "Pdmax" not in frame or "Pcmax" not in frame:
        raise ValueError("resource must be loaded with study-range power limits")
    discharge_limits = frame["Pdmax"].to_numpy(dtype=float)
    charge_limits = -frame["Pcmax"].to_numpy(dtype=float)
    availability = _available_rows(frame).to_numpy()
    study_prices = prices[: config.implementation_steps]
    price_min = (
        float(study_prices.min()) if resource.price_min is None else resource.price_min
    )
    price_max = (
        float(study_prices.max()) if resource.price_max is None else resource.price_max
    )
    modeled_soc = resource.initial_soc
    records: list[dict[str, object]] = []
    resource_date = pd.Timestamp(frame[TIME_COLUMN].iloc[0]).normalize()
    origin_date = (
        resource_date
        if innovation_origin_date is None
        else pd.Timestamp(innovation_origin_date).normalize()
    )
    day_offset = (resource_date - origin_date).days
    if not 0 <= day_offset < INNOVATION_DAY_CAPACITY:
        raise ValueError(
            f"{resource.name}: input date {resource_date.date()} is outside the "
            f"{INNOVATION_DAY_CAPACITY}-day innovation range beginning "
            f"{origin_date.date()}"
        )
    innovation_start = int((resource_date - origin_date) / pd.Timedelta(hours=1))
    required_rows = innovation_start + config.implementation_steps
    required_shape = (
        config.expected_standard_points * INNOVATION_DAY_CAPACITY,
        MAX_HORIZON_STEPS,
    )
    if innovations.shape != required_shape:
        raise ValueError(
            f"innovations has shape {innovations.shape}, expected {required_shape}"
        )
    if required_rows > len(innovations):
        raise ValueError(f"{resource.name}: innovation matrix is too short")

    for implementation_step in range(config.implementation_steps):
        horizon_slice = slice(
            implementation_step,
            implementation_step + config.horizon_steps,
        )
        timestamp = frame[TIME_COLUMN].iloc[implementation_step]
        observed_horizon = prices[horizon_slice]
        estimated_horizon = build_estimated_price_horizon(
            observed_horizon,
            innovations[
                innovation_start + implementation_step,
                : config.horizon_steps,
            ],
            meanstd_percent=config.meanstd_percent,
            error_increment=config.forecast_error_increment,
            price_min=price_min,
            price_max=price_max,
        )
        available = bool(availability[implementation_step])
        has_power = (
            discharge_limits[implementation_step] > 0
            or charge_limits[implementation_step] > 0
        )
        if available and has_power:
            bid = solve_bidding_function(
                resource=resource.name,
                timestamp=timestamp,
                estimated_prices=estimated_horizon,
                discharge_limits=discharge_limits[horizon_slice],
                charge_limits=charge_limits[horizon_slice],
                initial_soc=modeled_soc,
                minimum_soc=resource.minimum_soc,
                maximum_soc=resource.maximum_soc,
                config=config,
                solver_environment=solver_environment,
            )
        else:
            # Seven existing staircase columns; exported SOC is appended below.
            bid = BidResult(
                np.array([[price_min, price_max, 0, 0, 0, 0, 0]], dtype=float)
            )

        lmp = float(prices[implementation_step])
        cleared_stair, cleared_power = _clear_dsfunction(bid.dsfunction, lmp)
        soc_before = modeled_soc
        soc_after = _soc_after_power(soc_before, cleared_power, config)
        try:
            soc_after = _clip_soc_roundoff(
                soc_after,
                resource.minimum_soc,
                resource.maximum_soc,
                scale=max(abs(soc_before), abs(soc_after - soc_before)),
            )
        except ValueError as exc:
            raise RuntimeError(
                f"{resource.name} at {timestamp}: cleared power {cleared_power} "
                f"moves SOC from {soc_before} to {soc_after}, outside "
                f"[{resource.minimum_soc}, {resource.maximum_soc}]"
            ) from exc
        candidate_soc = np.array(
            [
                _clip_soc_roundoff(
                    _soc_after_power(soc_before, float(power), config),
                    resource.minimum_soc,
                    resource.maximum_soc,
                    scale=abs(soc_before),
                )
                for power in bid.dsfunction[:, 2]
            ],
            dtype=float,
        )
        exported_dsfunction = np.column_stack((bid.dsfunction, candidate_soc))
        current_pdmax = float(discharge_limits[implementation_step])
        current_pcmax = float(charge_limits[implementation_step])
        power_limit_reached = (
            cleared_power >= 0.0
            and current_pdmax > config.power_tolerance
            and math.isclose(
                cleared_power,
                current_pdmax,
                rel_tol=1e-9,
                abs_tol=config.power_tolerance,
            )
        ) or (
            cleared_power < 0.0
            and current_pcmax > config.power_tolerance
            and math.isclose(
                cleared_power,
                -current_pcmax,
                rel_tol=1e-9,
                abs_tol=config.power_tolerance,
            )
        )
        soc_boundary_reached = (
            cleared_power > config.power_tolerance
            and soc_after <= resource.minimum_soc + config.soc_tolerance
        ) or (
            cleared_power < -config.power_tolerance
            and soc_after >= resource.maximum_soc - config.soc_tolerance
        )
        record = {
            "Time": timestamp.tz_localize(None) if timestamp.tzinfo else timestamp,
            RESOURCE_COLUMN: resource.name,
            "Price Source": "member_lmp",
            LMP_COLUMN: lmp,
            HSL_COLUMN: float(hsl[implementation_step]),
            LSL_COLUMN: float(lsl[implementation_step]),
            "Pdmax": current_pdmax,
            "Pcmax": -current_pcmax,
            "Observed SOC": float(frame[OBSERVED_SOC_COLUMN].iloc[implementation_step]),
            "Modeled SOC Before": soc_before,
            "Applied Minimum SOC": resource.minimum_soc,
            "Applied Maximum SOC": resource.maximum_soc,
            "Cleared MW": cleared_power,
            "Modeled SOC After": soc_after,
            "Profit": lmp * cleared_power * config.interval_hours,
            "Power Limit Reached": power_limit_reached,
            "SOC Boundary Reached": soc_boundary_reached,
            "Cleared SOC Limit State": int(bid.dsfunction[cleared_stair, 6]),
            "dsfunction": json.dumps(
                exported_dsfunction.tolist(), separators=(",", ":")
            ),
        }
        if config.experiment_path is not None:
            record.update(
                {
                    experiment_data.STANDARD_TIME: timestamp,
                    experiment_data.SCED_TIME: frame["Original SCED Time Stamp"].iloc[
                        implementation_step
                    ],
                    experiment_data.STATUS: frame[experiment_data.STATUS].iloc[
                        implementation_step
                    ],
                    experiment_data.AVAILABLE: available,
                    experiment_data.EFFECTIVE_HSL: current_pdmax,
                    experiment_data.EFFECTIVE_LSL: -current_pcmax,
                    HSL_COLUMN: float(frame["Raw HSL"].iloc[implementation_step]),
                    LSL_COLUMN: float(frame["Raw LSL"].iloc[implementation_step]),
                }
            )
        record.update(
            experiment_data.result_metadata(frame.iloc[implementation_step].to_dict())
        )
        record["Normalized SOC Before"] = experiment_data.normalize_energy(
            soc_before, resource.maximum_soc
        )
        record["Normalized SOC After"] = experiment_data.normalize_energy(
            soc_after, resource.maximum_soc
        )
        records.append(record)
        modeled_soc = soc_after
        if (
            config.experiment_path is not None
            and (implementation_step + 1) % config.expected_standard_points == 0
        ):
            print(
                f"{resource.name}: {implementation_step + 1}/{config.implementation_steps} steps",
                flush=True,
            )
    return pd.DataFrame.from_records(
        records, columns=None if config.experiment_path is not None else OUTPUT_COLUMNS
    )


# ---------------------------------------------------------------------------
# Output assembly and CLI
# ---------------------------------------------------------------------------


def _safe_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return normalized or "resource"


def _atomic_excel_write(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, suffix=".xlsx", delete=False
        ) as handle:
            temporary_path = Path(handle.name)
        with pd.ExcelWriter(temporary_path, engine="openpyxl") as writer:
            frame.to_excel(writer, sheet_name="Rolling", index=False)
            worksheet = writer.book["Rolling"]
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions
            for index, column in enumerate(frame.columns, start=1):
                worksheet.column_dimensions[get_column_letter(index)].width = (
                    EXCEL_COLUMN_WIDTHS.get(column, max(18, min(len(column) + 2, 34)))
                )
            dsfunction_index = frame.columns.get_loc("dsfunction") + 1
            for row in range(2, len(frame) + 2):
                worksheet.cell(row, dsfunction_index).alignment = Alignment(
                    wrap_text=True,
                    vertical="top",
                )
            for column in (
                "Time",
                experiment_data.STANDARD_TIME,
                experiment_data.SCED_TIME,
            ):
                if column in frame:
                    time_index = frame.columns.get_loc(column) + 1
                    for row in range(2, len(frame) + 2):
                        worksheet.cell(row, time_index).number_format = (
                            "yyyy-mm-dd hh:mm:ss"
                        )
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _atomic_csv_write(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8-sig",
            newline="",
            dir=path.parent,
            suffix=".csv.tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            frame.to_csv(handle, index=False)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _config_payload(config: RollingConfig) -> dict[str, object]:
    payload = asdict(config)
    payload["data_dir"] = str(config.data_dir.resolve())
    payload["results_dir"] = str(config.results_dir.resolve())
    payload["experiment_path"] = (
        str(config.experiment_path.resolve()) if config.experiment_path else None
    )
    if config.experiment_path is not None:
        payload.pop("seed")  # This legacy scalar is unused in member-seeded mode.
        payload["random_policy"] = "per_member_fixed_registry"
    payload["study_start_date"] = config.study_start_date.isoformat()
    payload["study_end_date"] = config.study_end_date.isoformat()
    payload["planned_implementation_steps"] = config.planned_implementation_steps
    payload["required_study_points"] = config.required_study_points
    payload["interval_hours"] = config.interval_hours
    # Persist actual units and policies for independent result verification.
    payload["interval_minutes"] = 60
    payload["energy_unit"] = "MWh"
    payload["available_statuses"] = list(AVAILABLE_STATUSES)
    payload["soc_policy"] = SOC_POLICY
    payload["power_limit_policy"] = POWER_LIMIT_POLICY
    return payload


def _validate_rolling_result(frame: pd.DataFrame, config: RollingConfig) -> None:
    if experiment_data.SOC_POLICY_COLUMN not in frame or not frame[
        experiment_data.SOC_POLICY_COLUMN
    ].eq(SOC_POLICY).all():
        raise ValueError("Rolling requires monthly submitted SOC bounds")
    if experiment_data.POWER_POLICY not in frame or not frame[
        experiment_data.POWER_POLICY
    ].eq(experiment_data.SUBMITTED_POWER_POLICY).all():
        raise ValueError("Rolling requires monthly submitted power limits")
    experiment_data.validate_result_frame(
        frame,
        "Rolling",
        expected_start=pd.Timestamp(config.study_start_date),
        expected_steps=config.implementation_steps,
        efficiency=config.efficiency,
    )
    for result_col, limit_col in (
        ("Pcmax", experiment_data.EFFECTIVE_LSL),
        ("Pdmax", experiment_data.EFFECTIVE_HSL),
    ):
        if not np.array_equal(
            frame[result_col].to_numpy(), frame[limit_col].to_numpy()
        ):
            raise ValueError("Rolling power ratings disagree with effective limits")


def _summarize_rolling_result(frame: pd.DataFrame, config: RollingConfig, *, seed: int):
    _validate_rolling_result(frame, config)
    return experiment_data.summarize_frame(frame, "Rolling", seed=seed)


def _export_run(
    *,
    config: RollingConfig,
    resources: Sequence[ResourceData],
    skipped: tuple[str, ...],
    frames: Sequence[pd.DataFrame],
    started_at: datetime,
    random_path: Path | None,
    random_manifest_path: Path | None,
    innovation_origin_date: pd.Timestamp,
    random_records: tuple[experiment_data.RandomRecord, ...] = (),
    run_provenance: dict | None = None,
) -> DemoResult:
    """Write the shared serial/distributed output contract."""

    if len(resources) != len(frames):
        raise ValueError("resources and result frames must have equal lengths")
    for frame in frames:
        _validate_rolling_result(frame, config)
    detail_paths = [
        config.results_dir
        / config.output_name(f"{_safe_name(resource.name)}_Rolling_Window_1hour.xlsx")
        for resource in resources
    ]
    detail_owners: dict[Path, str] = {}
    for resource, detail_path in zip(resources, detail_paths):
        if detail_path in detail_owners:
            raise ValueError(
                "member names map to the same detail path: "
                f"{detail_owners[detail_path]!r} and {resource.name!r}"
            )
        detail_owners[detail_path] = resource.name
    for frame, detail_path in zip(frames, detail_paths):
        _atomic_excel_write(frame, detail_path)

    summary = pd.concat(frames, ignore_index=True)
    summary_path = config.results_dir / config.output_name(
        "Rolling_Window_Summary_1hour.csv"
    )
    _atomic_csv_write(summary, summary_path)

    completed_at = datetime.now(timezone.utc)
    manifest_path = config.results_dir / config.output_name("run_manifest.json")
    manifest = {
        "schema_version": 2,
        "status": "complete",
        "started_at_utc": started_at.isoformat(),
        "completed_at_utc": completed_at.isoformat(),
        "elapsed_seconds": (completed_at - started_at).total_seconds(),
        "configuration": _config_payload(config),
        "horizon_policy": "fixed_full_horizon",
        "power_limit_policy": POWER_LIMIT_POLICY,
        "available_statuses": list(AVAILABLE_STATUSES),
        "soc_policy": SOC_POLICY,
        "input_power_limits": {
            resource.name: resource.frame.attrs.get("input_power_limits", {})
            for resource in resources
        },
        "processed_resources": [resource.name for resource in resources],
        "skipped_no_lmp": list(skipped),
        "skipped_coverage": list(skipped),
        "source_files": [str(resource.source_path.resolve()) for resource in resources],
        "random_sequence": (
            None
            if random_path is None
            else {
                "path": str(random_path.resolve()),
                "sha256": _sha256(random_path),
                "manifest_path": str(random_manifest_path.resolve()),
                "origin_date": innovation_origin_date.date().isoformat(),
            }
        ),
        "outputs": {
            "summary": str(summary_path.resolve()),
            "details": [str(path.resolve()) for path in detail_paths],
        },
    }
    if config.experiment_path is not None:
        experiment = experiment_data.load_experiment(
            config.experiment_path, config.month
        )
        manifest["schema_version"] = 3
        manifest["random_sequences"] = {r.member: r.as_dict() for r in random_records}
        manifest["provenance"] = run_provenance or experiment_data.provenance(
            experiment, [r.source_path for r in resources]
        )
        manifest["gurobi_version"] = list(gp.gurobi.version())
        manifest["solver_parameters"] = {"Seed": 0, "Threads": config.solver_threads}
        summaries = []
        by_member = {r.member: r for r in random_records}
        for frame in frames:
            member = str(frame[RESOURCE_COLUMN].iloc[0])
            random_record = by_member[member]
            row = _summarize_rolling_result(frame, config, seed=random_record.seed)
            row.update(
                random_path=str(random_record.path.resolve()),
                random_sha256=random_record.sha256,
            )
            summaries.append(row)
        _atomic_csv_write(
            pd.DataFrame(summaries),
            config.results_dir / config.output_name("member_summary_1hour.csv"),
        )
    _atomic_json_dump(manifest, manifest_path)
    return DemoResult(
        detail_paths=tuple(detail_paths),
        summary_path=summary_path,
        manifest_path=manifest_path,
        random_path=random_path,
        skipped_resources=skipped,
        random_records=random_records,
    )


def run_demo(
    config: RollingConfig = RollingConfig(),
    requested_members: Sequence[str] | None = None,
) -> DemoResult:
    """Run the validated rolling demo and atomically export successful results."""

    config = validate_config(config)
    started_at = datetime.now(timezone.utc)
    resources, skipped, innovation_origin_date = load_resources(
        config,
        requested_members,
    )
    random_records = ()
    if config.experiment_path is not None:
        experiment = experiment_data.load_experiment(
            config.experiment_path, config.month
        )
        random_records = tuple(
            experiment_data.prepare_random(experiment, r.name) for r in resources
        )
        random_path = random_manifest_path = None

    run_provenance = begin_experiment_run(config, resources)

    result_frames: list[pd.DataFrame] = []
    for resource in resources:
        if random_records:
            record = next(r for r in random_records if r.member == resource.name)
            innovations = np.load(record.path, allow_pickle=False, mmap_mode="r")
        result_frames.append(
            run_resource(
                resource,
                innovations,
                config,
                innovation_origin_date=innovation_origin_date,
                solver_threads=config.solver_threads,
            )
        )
    return _export_run(
        config=config,
        resources=resources,
        skipped=skipped,
        frames=result_frames,
        started_at=started_at,
        random_path=random_path,
        random_manifest_path=random_manifest_path,
        innovation_origin_date=innovation_origin_date,
        random_records=random_records,
        run_provenance=run_provenance,
    )


def begin_experiment_run(
    config: RollingConfig, resources: Sequence[ResourceData]
) -> dict | None:
    """Capture inputs/code before solving and invalidate any previous completion."""
    if config.experiment_path is None:
        return None
    experiment = experiment_data.load_experiment(config.experiment_path, config.month)
    metadata = experiment_data.provenance(
        experiment, [r.source_path for r in resources]
    )
    experiment_data.atomic_json(
        config.results_dir / config.output_name("run_manifest.json"),
        {
            "status": "running",
            "provenance": metadata,
            "configuration": _config_payload(config),
            "processed_resources": [r.name for r in resources],
        },
    )
    return metadata


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--experiment",
        type=Path,
        default=DEFAULT_EXPERIMENT_PATH,
        help="experiment configuration JSON (default: beside this script)",
    )
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--month", help="YYYY-MM; omit to run every configured month")
    parser.add_argument(
        "--workers", type=int, default=12, help="worker processes (default: 12)"
    )
    parser.add_argument("--solver-threads", type=int, default=1)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="input directory; must agree with the experiment configuration",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=None,
        help="directory for detailed, summary, manifest, and random outputs",
    )
    parser.add_argument(
        "--member",
        action="append",
        default=None,
        help="resource name to run; repeat for multiple resources",
    )
    parser.add_argument(
        "--steps",
        type=int,
        default=None,
        help="optional total implementation-step cap for smoke tests",
    )
    parser.add_argument(
        "--horizon",
        type=int,
        default=None,
        help=f"fixed look-ahead length in 1..{MAX_HORIZON_STEPS}",
    )
    parser.add_argument("--price-samples", type=int, default=None)
    parser.add_argument("--seed", type=int, default=None)
    return parser


def config_from_arguments(arguments: argparse.Namespace) -> RollingConfig:
    """Resolve CLI overrides once for serial and distributed entrypoints."""
    if arguments.experiment is not None:
        experiment = experiment_data.load_experiment(
            arguments.experiment, arguments.month
        )
        names = experiment_data.selected_names(experiment, arguments.member)
        if arguments.seed is not None and any(
            experiment.member_seeds[n] != arguments.seed for n in names
        ):
            raise ValueError(
                "--seed conflicts with fixed member seeds in experiment configuration"
            )
        if (
            arguments.data_dir is not None
            and arguments.data_dir.resolve() != experiment.data_dir
        ):
            raise ValueError("--data-dir conflicts with experiment configuration")
        return validate_config(
            RollingConfig(
                experiment_path=experiment.path,
                month=arguments.month,
                data_dir=experiment.data_dir,
                results_dir=arguments.results_dir or experiment.output_root / "rolling",
                implementation_steps=arguments.steps,
                horizon_steps=(
                    experiment.horizon_steps
                    if arguments.horizon is None
                    else arguments.horizon
                ),
                price_samples=(
                    experiment.price_samples
                    if arguments.price_samples is None
                    else arguments.price_samples
                ),
                solver_threads=arguments.solver_threads,
            )
        )
    return validate_config(
        RollingConfig(
            data_dir=arguments.data_dir or DEFAULT_DATA_DIR,
            results_dir=arguments.results_dir or DEFAULT_RESULTS_DIR,
            implementation_steps=arguments.steps,
            horizon_steps=(
                DEFAULT_HORIZON_STEPS
                if arguments.horizon is None
                else arguments.horizon
            ),
            price_samples=(
                300 if arguments.price_samples is None else arguments.price_samples
            ),
            seed=42 if arguments.seed is None else arguments.seed,
            solver_threads=arguments.solver_threads,
        )
    )


def validate_inputs_only(
    config: RollingConfig, members: Sequence[str] | None, *, workers: int = 12
) -> None:
    if config.experiment_path is not None:
        _load_experiment_resources(config, members, audit_all=False, workers=workers)
    else:
        load_resources(config, members)
    print("Input validation complete; no optimization performed.", flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point for the full demo and reduced validation runs."""

    arguments = _build_argument_parser().parse_args(argv)
    from distributed_Rolling_Window_1hour import run_distributed

    run_cli_months(arguments, run_distributed)
    return 0


def run_cli_months(arguments: argparse.Namespace, runner) -> None:
    """Common CLI batch selection for both Rolling entrypoints."""
    months = experiment_data.experiment_months(arguments.experiment, arguments.month)
    results_root = arguments.results_dir
    failures = []
    for month in months:
        selected = argparse.Namespace(**vars(arguments))
        selected.month = month
        if month is not None and results_root is not None:
            selected.results_dir = results_root / "rolling"
        config = config_from_arguments(selected)
        if selected.validate_only:
            try:
                validate_inputs_only(config, selected.member, workers=selected.workers)
            except ValueError as exc:
                failures.append(f"{month}: {exc}")
                print(f"Validation failed for {month}: {exc}", flush=True)
        else:
            result = runner(
                config,
                selected.member,
                workers=selected.workers,
                solver_threads=selected.solver_threads,
            )
            print(
                f"Completed {month}: {len(result.detail_paths)} members; summary={result.summary_path}",
                flush=True,
            )
    if months[0] is not None:
        from Monthly_Data_1hour import write_batch_summary

        write_batch_summary(
            arguments.experiment,
            months,
            "rolling",
            validate_only=arguments.validate_only,
            results_root=results_root,
        )
    if failures:
        raise ValueError("monthly validation failures: " + "; ".join(failures))


if __name__ == "__main__":
    raise SystemExit(main())
