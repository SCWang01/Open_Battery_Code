"""Shared ERCOT experiment contracts, validation, random files and reports.

This module has no solver dependency. Experiment-mode callers consume the same
validated hourly grid and explicit monthly energy policy. Source data
remain five-minute records; model states and settlements are hourly.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd


SOURCE_TIME = "Standard 5-Minute Time Stamp"
STANDARD_TIME = "Hour Start"
TELEMETRY = "Telemetered Net Output"
PARTICIPATING = "Market Participating"
ZERO_DAY = "Zero Output Day"
CAPACITY = "Capacity MWh"
USABLE_CAPACITY = "Usable Capacity MWh"
SOC_POLICY_COLUMN = "SOC Policy"
TELEMETRY_SOC_POLICY = "telemetry_reconstructed"
SUBMITTED_SOC_POLICY = "monthly_raw_submitted_soc_bounds_initial_observed"
ZERO_MIN_SOC_POLICY = "monthly_zero_min_submitted_max_soc_initial_observed"
SUBMITTED_SOC_POLICIES = (SUBMITTED_SOC_POLICY, ZERO_MIN_SOC_POLICY)
INITIAL_ENERGY = "Initial Energy MWh"
RATED_CHARGE = "Rated Charge MW"
RATED_DISCHARGE = "Rated Discharge MW"
POWER_POLICY = "Power Limit Policy"
TELEMETRY_POWER_POLICY = "monthly_hourly_telemetry_extrema_ceil_0p1"
SUBMITTED_POWER_POLICY = "monthly_raw_submitted_HSL_max_LSL_min"
SUBMITTED_HIGH = "Submitted HSL Maximum"
SUBMITTED_LOW = "Submitted LSL Minimum"
INTERVAL_HOURS = 1.0
SCED_TIME = "SCED Time Stamp"
STATUS = "Telemetered Resource Status"
AVAILABLE = "Trading Available"
EFFECTIVE_HSL = "Effective HSL"
EFFECTIVE_LSL = "Effective LSL"
IS_IMPLEMENTATION = "Is Implementation"
SOURCE_ROW = "Source Row"
TIME_FORMAT = "%m/%d/%Y %H:%M:%S"
POINTS_PER_DAY = 24
RANDOM_SHAPE = (31 * POINTS_PER_DAY, 36)
NUMERIC_COLUMNS = ("LMP", "HSL", "LSL", "State of Charge", "Minimum SOC", "Maximum SOC")
CURVE_PAIRS = tuple(
    (f"Submitted TPO-MW{i}", f"Submitted TPO-Price{i}") for i in range(1, 11)
)
CURVE_COLUMNS = tuple(c for pair in CURVE_PAIRS for c in pair)

# Fixed cohort exclusions approved for the six-month hourly experiment.
EXCLUDED_MONTHLY_MEMBERS = frozenset(
    {
        "UTOPIA_ESR1",
        "BLSUMMIT_ESR2",
        "E_HARRIS_ESR1",
        "HEADCAMP_ESR1",
        "HEARN_RD_ESR1",
        "JAG_SLR_ESR2",
        "JOHNCI_ESR1",
        "MIDP_SLR_ESR1",
        "PALACIOS_ESR1",
        "RHA_ESR1",
        "Z0_ESR1",
    }
)


@dataclass(frozen=True)
class Experiment:
    """Validated settings; relative paths resolve beside config."""

    path: Path
    data_dir: Path
    output_root: Path
    start: pd.Timestamp
    end_exclusive: pd.Timestamp
    origin: pd.Timestamp
    member_seeds: dict[str, int]
    horizon_steps: int
    price_samples: int
    efficiency: float
    meanstd_percent: float
    forecast_error_increment: float
    payload: dict

    @property
    def steps(self) -> int:
        return int((self.end_exclusive - self.start) / pd.Timedelta(hours=1))

    @property
    def random_dir(self) -> Path:
        return self.output_root / "random"

    def output_name(self, filename: str) -> str:
        """Namespace monthly artifacts by filename while keeping legacy names."""
        return monthly_output_name(filename, self.payload.get("month"))


def monthly_output_name(filename: str, month: str | None) -> str:
    if month is None:
        return filename
    path = Path(filename)
    return f"{path.stem}_{month.replace('-', '')}{path.suffix}"


@dataclass(frozen=True)
class MemberData:
    name: str
    source_path: Path
    frame: pd.DataFrame
    minimum_soc: float
    maximum_soc: float
    initial_soc: float
    price_min: float
    price_max: float
    audit: dict


@dataclass(frozen=True)
class RandomRecord:
    member: str
    seed: int
    path: Path
    manifest_path: Path
    sha256: str

    def as_dict(self) -> dict:
        return {
            "member": self.member,
            "seed": self.seed,
            "path": str(self.path.resolve()),
            "manifest_path": str(self.manifest_path.resolve()),
            "sha256": self.sha256,
        }


def _integer(value, name: str, lower: int, upper: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not lower <= value <= upper
    ):
        raise ValueError(f"{name} must be an integer in {lower}..{upper}: {value!r}")
    return value


def experiment_months(
    path: Path | str, month: str | None = None
) -> tuple[str | None, ...]:
    """Resolve a batch selection without opening any input workbooks."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") == 2:
        expected = payload["study_start_date"][:7]
        if month is not None and month != expected:
            raise ValueError(f"month must be {expected} for this single-month config")
        return (None,)
    if payload.get("schema_version") != 3:
        raise ValueError("unsupported experiment schema_version")
    months = payload.get("months", [])
    if (
        not months
        or len(set(months)) != len(months)
        or any(
            not isinstance(m, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", m)
            for m in months
        )
    ):
        raise ValueError("months must contain unique YYYY-MM values")
    if month is not None and month not in months:
        raise ValueError(f"month {month!r} is not in configured months {months}")
    return (month,) if month is not None else tuple(sorted(months))


def load_experiment(path: Path | str, month: str | None = None) -> Experiment:
    """Reject invalid policy, dates, identities and seed collisions upfront."""
    path = Path(path).resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    monthly = payload.get("schema_version") == 3
    if monthly:
        choices = experiment_months(path, month)
        if len(choices) != 1:
            raise ValueError("select a month when loading one monthly experiment")
        month = choices[0]
        if payload.get("timezone") != "America/Chicago":
            raise ValueError("monthly timezone must be America/Chicago")
        registry = payload.get("members", {})
        excluded = (
            EXCLUDED_MONTHLY_MEMBERS.intersection(registry)
            if isinstance(registry, dict)
            else set()
        )
        if excluded:
            raise ValueError(
                f"members excluded from all monthly experiments: {sorted(excluded)}"
            )
        start = pd.Timestamp(month + "-01", tz=payload["timezone"])
        end_exclusive = start + pd.offsets.MonthBegin(1)
        payload = {
            **payload,
            "month": month,
            "study_start_date": start.date().isoformat(),
            "study_end_date": (end_exclusive - pd.Timedelta(hours=1))
            .date()
            .isoformat(),
            "innovation_origin_date": start.date().isoformat(),
        }
    elif payload.get("schema_version") != 2:
        raise ValueError("unsupported experiment schema_version")
    elif month is not None:
        experiment_months(path, month)
    for key, expected in (
        ("available_status", "hourly_any_ON_ONTEST"),
        ("source_interval_minutes", 5),
        ("interval_minutes", 60),
        ("power_rounding_mw", 0.1),
        ("capacity_rounding_mwh", 0.1),
        ("zero_day_policy", "all_hourly_power_zero"),
        ("control_share", 1),
    ):
        if payload.get(key) != expected:
            raise ValueError(f"{key} must be {expected!r}")
    if payload.get("soc_policy") not in (TELEMETRY_SOC_POLICY, *SUBMITTED_SOC_POLICIES):
        raise ValueError("unknown soc_policy")
    if payload["soc_policy"] in SUBMITTED_SOC_POLICIES and payload["schema_version"] != 3:
        raise ValueError("submitted SOC bounds require monthly schema_version=3")
    dates = []
    for key in ("study_start_date", "study_end_date", "innovation_origin_date"):
        value = payload.get(key, "")
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError(f"{key} must be YYYY-MM-DD")
        dates.append(pd.Timestamp(value))
    start, end, origin = dates
    if not origin <= start <= end < origin + pd.Timedelta(days=31):
        raise ValueError(
            "study dates must be ordered within the 31-day innovation grid"
        )
    if start.day != 1 or end != start + pd.offsets.MonthEnd(0):
        raise ValueError("hourly experiment must cover one complete calendar month")
    if monthly:
        start = start.tz_localize(payload["timezone"])
        end_exclusive = start + pd.offsets.MonthBegin(1)
        origin = start
    else:
        end_exclusive = end + pd.Timedelta(days=1)
    seeds = payload.get("members")
    if not isinstance(seeds, dict) or not seeds:
        raise ValueError("members must be a nonempty member-to-seed mapping")
    for name, seed in seeds.items():
        if not re.fullmatch(r"[A-Za-z0-9_]+", name):
            raise ValueError(f"invalid member name: {name!r}")
        _integer(seed, f"seed for {name}", 0, 2**32 - 1)
    if len(set(seeds.values())) != len(seeds):
        raise ValueError("member seeds must be unique")
    horizon = _integer(payload.get("horizon_steps", 24), "horizon_steps", 1, 36)
    if monthly and horizon > 25:
        raise ValueError(
            "monthly input has only one day of tail data; horizon must be <= 25"
        )
    samples = _integer(payload.get("price_samples", 300), "price_samples", 2, 100000)
    efficiency = float(payload.get("efficiency", 0.95))
    meanstd = float(payload.get("meanstd_percent", 2.0))
    increment = float(payload.get("forecast_error_increment", 0.001))
    if not 0 < efficiency <= 1 or not all(
        math.isfinite(x) and x >= 0 for x in (meanstd, increment)
    ):
        raise ValueError("invalid efficiency or forecast error parameters")
    if efficiency != 0.95:
        raise ValueError(
            "this shared Benchmark/Rolling experiment requires efficiency=0.95"
        )
    if payload.get("solver_seed", 0) != 0 or payload.get("solver_threads", 1) != 1:
        raise ValueError("experiment requires solver_seed=0 and solver_threads=1")
    data_dir = (path.parent / payload["data_dir"]).resolve()
    if not data_dir.is_dir():
        raise FileNotFoundError(f"input directory does not exist: {data_dir}")
    return Experiment(
        path,
        data_dir,
        (path.parent / payload["output_root"]).resolve(),
        start,
        end_exclusive,
        origin,
        dict(seeds),
        horizon,
        samples,
        efficiency,
        meanstd,
        increment,
        payload,
    )


def selected_names(
    experiment: Experiment, requested: Sequence[str] | None
) -> tuple[str, ...]:
    names = set(experiment.member_seeds if requested is None else requested)
    unknown = names - experiment.member_seeds.keys()
    if not names or unknown:
        raise ValueError(
            f"requested members are not in fixed experiment registry: {sorted(unknown)}"
        )
    return tuple(sorted(names))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, payload: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _read_frame(path: Path) -> pd.DataFrame:
    # NA is an actual resource-status code, not a missing-value sentinel.
    if path.suffix.lower() == ".xlsx":
        return pd.read_excel(path, keep_default_na=False)
    return pd.read_csv(path, keep_default_na=False)


def _curve_validity(frame: pd.DataFrame) -> np.ndarray:
    """Same ordered, paired, finite-point contract as Benchmark._parse_curve."""
    valid = np.ones(len(frame), dtype=bool)
    any_point = np.zeros(len(frame), dtype=bool)
    last_mw = np.full(len(frame), -np.inf)
    last_price = np.full(len(frame), -np.inf)
    for mw_col, price_col in CURVE_PAIRS:
        mw_text = frame[mw_col].astype("string").fillna("").str.strip()
        price_text = frame[price_col].astype("string").fillna("").str.strip()
        present = mw_text.ne("").to_numpy() | price_text.ne("").to_numpy()
        mw = pd.to_numeric(mw_text, errors="coerce").to_numpy(dtype=float)
        price = pd.to_numeric(price_text, errors="coerce").to_numpy(dtype=float)
        valid &= ~present | (
            np.isfinite(mw)
            & np.isfinite(price)
            & (mw >= last_mw)
            & (price >= last_price)
        )
        last_mw = np.where(present, mw, last_mw)
        last_price = np.where(present, price, last_price)
        any_point |= present
    return valid & any_point


def ceil_tenth(value: float) -> float:
    """Round a nonnegative physical magnitude upward to a decimal tenth."""
    from decimal import Decimal, ROUND_CEILING

    if not math.isfinite(value) or value < 0:
        raise ValueError("rounding magnitude must be finite and nonnegative")
    return float(Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_CEILING))


def reconstruct_ess(power: np.ndarray, efficiency: float = 0.95) -> dict[str, float]:
    """Infer monthly ratings, capacity and initial MWh from hourly net power."""
    power = np.asarray(power, dtype=float)
    if power.ndim != 1 or not power.size or not np.isfinite(power).all():
        raise ValueError("hourly power must be a nonempty finite vector")
    if not math.isfinite(efficiency) or not 0 < efficiency <= 1:
        raise ValueError("efficiency must lie in (0, 1]")
    energy = np.r_[
        0.0,
        np.cumsum(
            -np.where(power >= 0, power / efficiency, power * efficiency)
            * INTERVAL_HOURS
        ),
    ]
    return {
        "rated_charge_mw": ceil_tenth(max(0.0, -float(power.min()))),
        "rated_discharge_mw": ceil_tenth(max(0.0, float(power.max()))),
        "capacity_mwh": ceil_tenth(float(np.ptp(energy))),
        "initial_energy_mwh": max(0.0, -float(energy.min())),
    }


def submitted_soc_parameters(
    raw: pd.DataFrame, *, soc_policy: str = SUBMITTED_SOC_POLICY
) -> dict[str, float | str]:
    """Derive exact energy bounds from chronologically sorted in-month source rows.

    The zero-min policy fixes the lower bound at zero; both policies retain the
    raw Maximum SOC maximum. The caller owns month filtering and ordering.
    No missing initial observation is substituted, and no observed energy or
    submitted bound is rounded/clipped.
    """
    if soc_policy not in SUBMITTED_SOC_POLICIES:
        raise ValueError(f"unknown submitted SOC policy: {soc_policy}")
    if raw.empty:
        raise ValueError("no current-month SOC source records")
    numbers = {}
    for column in ("State of Charge", "Minimum SOC", "Maximum SOC"):
        if column not in raw:
            raise ValueError(f"missing SOC column: {column}")
        original = raw[column]
        numeric = pd.to_numeric(original, errors="coerce")
        invalid = (
            ~np.isfinite(numeric.to_numpy(dtype=float))
            | original.map(lambda v: isinstance(v, (bool, np.bool_))).to_numpy()
        )
        if invalid.any():
            raise ValueError(f"invalid monthly {column}")
        numbers[column] = numeric
    minimum = (
        0.0
        if soc_policy == ZERO_MIN_SOC_POLICY
        else float(numbers["Minimum SOC"].min())
    )
    maximum = float(numbers["Maximum SOC"].max())
    initial = float(numbers["State of Charge"].iloc[0])
    if not 0 <= minimum <= maximum:
        raise ValueError(f"invalid monthly SOC bounds: [{minimum}, {maximum}]")
    if not minimum <= initial <= maximum:
        raise ValueError(f"initial observed SOC {initial} outside [{minimum}, {maximum}]")
    return {
        "minimum_energy_mwh": minimum,
        "maximum_energy_mwh": maximum,
        "capacity_mwh": maximum,
        "usable_capacity_mwh": maximum - minimum,
        "initial_energy_mwh": initial,
        "soc_policy": soc_policy,
    }


def submitted_power_parameters(raw: pd.DataFrame, power_policy: str) -> dict[str, float]:
    """Validate current-month raw submissions before any averaging or coercion."""
    if power_policy == TELEMETRY_POWER_POLICY:
        return {}
    if power_policy != SUBMITTED_POWER_POLICY:
        raise ValueError(f"unknown power policy: {power_policy}")
    ratings = {}
    for column, key, sign in (
        ("HSL", "rated_discharge_mw", 1),
        ("LSL", "rated_charge_mw", -1),
    ):
        if column not in raw:
            raise ValueError(f"missing submitted power column: {column}")
        original = raw[column]
        blank = original.isna() | original.astype(str).str.strip().eq("")
        numeric = pd.to_numeric(original, errors="coerce")
        invalid = (
            ~np.isfinite(numeric.to_numpy(dtype=float))
            | original.map(lambda v: isinstance(v, (bool, np.bool_))).to_numpy()
        ) & ~blank.to_numpy()
        if invalid.any():
            raise ValueError(f"invalid nonblank submitted {column}")
        valid = numeric.loc[~blank]
        if valid.empty:
            raise ValueError(f"no valid monthly submitted {column}")
        # Individual submissions may cross zero; only the monthly envelope is used.
        rating = float((valid * sign).max())
        if rating < 0:
            raise ValueError(f"invalid monthly submitted {column} bound")
        ratings[key] = rating
    return ratings


def add_submitted_hourly_extrema(hourly, raw, hour_column):
    """Retain raw hourly extrema so full-month exports can verify the ratings."""
    groups = raw.groupby(hour_column, sort=True)
    hourly[SUBMITTED_HIGH] = groups["HSL"].max().to_numpy(dtype=float)
    hourly[SUBMITTED_LOW] = groups["LSL"].min().to_numpy(dtype=float)
    hourly[POWER_POLICY] = SUBMITTED_POWER_POLICY


def result_metadata(row: dict) -> dict:
    """Fields shared by Benchmark and Rolling hourly result records."""
    columns = (
        STANDARD_TIME,
        STATUS,
        PARTICIPATING,
        ZERO_DAY,
        AVAILABLE,
        CAPACITY,
        INITIAL_ENERGY,
        RATED_CHARGE,
        RATED_DISCHARGE,
        TELEMETRY,
        "Observed SOC",
        "Observed Minimum SOC",
        "Observed Maximum SOC",
        "Source Status Counts",
        "Source Rows",
        "Source SCED Times",
        EFFECTIVE_HSL,
        EFFECTIVE_LSL,
    )
    result = {col: row[col] for col in columns}
    for col in (
        SOC_POLICY_COLUMN,
        USABLE_CAPACITY,
        POWER_POLICY,
        SUBMITTED_HIGH,
        SUBMITTED_LOW,
        "Hour Start UTC",
        "Source Standard Times",
        "Source Record Count",
        "Power Sample Count",
        "Price Sample Count",
        "Source Coverage",
        "Power Coverage",
        "Price Coverage",
        "Month",
    ):
        if col in row:
            result[col] = row[col]
    # Excel has no timezone-aware datetime type. UTC is exported explicitly.
    if "Hour Start UTC" in result:
        result[STANDARD_TIME] = pd.Timestamp(result[STANDARD_TIME]).tz_localize(None)
    return result


def normalize_energy(energy: float, capacity: float) -> float:
    return energy / capacity if capacity else 0.0


def inspect_member(
    path: Path, experiment: Experiment, tail_steps: int = 0,
    *, power_policy: str = TELEMETRY_POWER_POLICY,
    soc_policy: str = TELEMETRY_SOC_POLICY,
) -> tuple[MemberData | None, dict]:
    """Validate five-minute sources, then derive one shared hourly ESS contract.

    Full calendar days of tail telemetry determine day-zero gates. Only the
    requested tail prices are required. Monthly callers select their energy and
    power policies explicitly; forecast-tail rows never set monthly parameters.
    """
    _integer(tail_steps, "tail_steps", 0, 35)
    if experiment.payload.get("schema_version") == 3:
        from Monthly_Data_1hour import inspect_monthly_member

        return inspect_monthly_member(
            path, experiment, tail_steps, power_policy=power_policy,
            soc_policy=soc_policy,
        )
    if soc_policy != TELEMETRY_SOC_POLICY:
        raise ValueError("submitted SOC bounds require monthly inputs")
    name = path.stem.removesuffix("_Usage")
    audit = {"member": name, "source": str(path.resolve()), "interval_minutes": 60}
    try:
        raw = _read_frame(path)
        required = {
            SCED_TIME,
            STATUS,
            "Resource Name",
            "LMP",
            TELEMETRY,
            *CURVE_COLUMNS,
        }
        if path.suffix.lower() == ".xlsx":
            required.add(SOURCE_TIME)
        if missing := required - set(raw.columns):
            raise ValueError(f"missing columns: {sorted(missing)}")
        raw[SOURCE_ROW] = np.arange(2, len(raw) + 2)
        raw[SCED_TIME] = pd.to_datetime(raw[SCED_TIME], errors="raise")
        if SOURCE_TIME not in raw:
            raw = raw.loc[raw[SCED_TIME].dt.minute.mod(5).eq(0)].copy()
            raw[SOURCE_TIME] = raw[SCED_TIME].dt.floor("5min")
        else:
            raw[SOURCE_TIME] = pd.to_datetime(raw[SOURCE_TIME], errors="raise")
        needed_end = experiment.end_exclusive + pd.Timedelta(hours=tail_steps)
        full_day_end = needed_end.ceil("D")
        selected = raw.loc[
            raw[SOURCE_TIME].ge(experiment.start) & raw[SOURCE_TIME].lt(full_day_end)
        ].copy()
        selected.sort_values(SOURCE_TIME, kind="stable", inplace=True)
        selected.reset_index(drop=True, inplace=True)
        expected = pd.date_range(
            experiment.start, full_day_end, freq="5min", inclusive="left"
        )
        if not pd.DatetimeIndex(selected[SOURCE_TIME]).equals(expected):
            raise ValueError(
                "duplicate, missing or misaligned five-minute source slots"
            )
        if not selected["Resource Name"].astype(str).str.strip().eq(name).all():
            raise ValueError("resource identity does not match filename")
        for col in (TELEMETRY, "LMP"):
            original = selected[col]
            numeric = pd.to_numeric(original, errors="coerce")
            needed = (
                np.ones(len(selected), dtype=bool)
                if col == TELEMETRY
                else selected[SOURCE_TIME].lt(needed_end).to_numpy()
            )
            invalid = original.map(
                lambda v: isinstance(v, (bool, np.bool_))
            ).to_numpy() | ~np.isfinite(numeric.to_numpy(dtype=float))
            if np.any(needed & invalid):
                bad = selected.loc[needed & invalid, SOURCE_TIME].iloc[0]
                raise ValueError(f"invalid {col} at {bad}")
            selected[col] = numeric
        selected[STATUS] = (
            selected[STATUS].fillna("").astype(str).str.strip().str.upper()
        )
        selected[STANDARD_TIME] = selected[SOURCE_TIME].dt.floor("h")
        submitted = submitted_power_parameters(
            selected.loc[selected[SOURCE_TIME].lt(experiment.end_exclusive)], power_policy
        )
        for col in ("HSL", "LSL", "State of Charge", "Minimum SOC", "Maximum SOC"):
            selected[col] = (
                pd.to_numeric(selected[col], errors="coerce")
                if col in selected
                else np.nan
            )
        records = []
        for hour, group in selected.groupby(STANDARD_TIME, sort=True):
            counts = group[STATUS].replace("", "<BLANK>").value_counts().to_dict()
            records.append(
                {
                    STANDARD_TIME: hour,
                    "Resource Name": name,
                    "LMP": float(group["LMP"].mean()),
                    TELEMETRY: float(group[TELEMETRY].mean()),
                    PARTICIPATING: bool(group[STATUS].isin(("ON", "ONTEST")).any()),
                    STATUS: "|".join(sorted(counts)),
                    "Source Status Counts": json.dumps(counts),
                    "Source Rows": json.dumps(group[SOURCE_ROW].astype(int).tolist()),
                    "Source SCED Times": json.dumps(
                        group[SCED_TIME].astype(str).tolist()
                    ),
                    SCED_TIME: group[SCED_TIME].iloc[0],
                    SOURCE_ROW: int(group[SOURCE_ROW].iloc[0]),
                    "HSL": float(group["HSL"].mean()),
                    "LSL": float(group["LSL"].mean()),
                    "State of Charge": float(group["State of Charge"].iloc[0]),
                    "Observed SOC": float(group["State of Charge"].iloc[0]),
                    "Observed Minimum SOC": float(group["Minimum SOC"].min()),
                    "Observed Maximum SOC": float(group["Maximum SOC"].max()),
                    "Source Curves": group[list(CURVE_COLUMNS)].to_dict(
                        orient="records"
                    ),
                }
            )
        hourly = pd.DataFrame(records)
        if submitted:
            add_submitted_hourly_extrema(hourly, selected, STANDARD_TIME)
        hourly[ZERO_DAY] = hourly.groupby(hourly[STANDARD_TIME].dt.normalize())[
            TELEMETRY
        ].transform(lambda v: bool(v.eq(0).all()))
        implementation = hourly.iloc[: experiment.steps]
        parameters = reconstruct_ess(
            implementation[TELEMETRY].to_numpy(dtype=float), experiment.efficiency
        )
        parameters.update(submitted)
        audit["power_limit_policy"] = power_policy
        hourly[AVAILABLE] = hourly[PARTICIPATING] & ~hourly[ZERO_DAY]
        hourly[EFFECTIVE_HSL] = np.where(
            hourly[AVAILABLE], parameters["rated_discharge_mw"], 0.0
        )
        hourly[EFFECTIVE_LSL] = np.where(
            hourly[AVAILABLE], -parameters["rated_charge_mw"], 0.0
        )
        hourly[CAPACITY] = parameters["capacity_mwh"]
        hourly[INITIAL_ENERGY] = parameters["initial_energy_mwh"]
        hourly[RATED_CHARGE] = parameters["rated_charge_mw"]
        hourly[RATED_DISCHARGE] = parameters["rated_discharge_mw"]
        hourly[IS_IMPLEMENTATION] = hourly[STANDARD_TIME].lt(experiment.end_exclusive)
        frame = hourly.iloc[: experiment.steps + tail_steps].copy()
        prices = frame["LMP"].iloc[: experiment.steps]
        audit.update(parameters)
        audit.update(
            valid=True,
            reason="valid",
            implementation_steps=experiment.steps,
            tail_steps=tail_steps,
            source_rows=len(selected),
            participating_hours=int(
                frame[PARTICIPATING].iloc[: experiment.steps].sum()
            ),
            trading_hours=int(frame[AVAILABLE].iloc[: experiment.steps].sum()),
        )
        frame.attrs["ess_parameters"] = parameters
        return (
            MemberData(
                name,
                path,
                frame,
                0.0,
                parameters["capacity_mwh"],
                parameters["initial_energy_mwh"],
                float(prices.min()),
                float(prices.max()),
                audit,
            ),
            audit,
        )
    except (ValueError, KeyError, TypeError) as exc:
        audit.update(valid=False, reason=str(exc))
        return None, audit


def discover_member_paths(experiment: Experiment) -> dict[str, Path]:
    """Resolve resource identities without reading the workbooks."""
    paths: dict[str, Path] = {}
    if experiment.payload.get("schema_version") == 3:
        suffix = "_Usage_" + experiment.payload["month"].replace("-", "")
        for path in sorted(experiment.data_dir.glob(f"*{suffix}.xlsx")):
            name = path.stem.removesuffix(suffix)
            if name in experiment.member_seeds:
                paths[name] = path
        return paths
    for path in sorted(experiment.data_dir.iterdir()):
        if path.suffix.lower() not in (".xlsx", ".csv") or not path.stem.endswith(
            "_Usage"
        ):
            continue
        name = path.stem.removesuffix("_Usage")
        if name in paths:
            raise ValueError(f"duplicate resource files: {paths[name]} and {path}")
        paths[name] = path
    return paths


def load_members(
    experiment: Experiment,
    requested: Sequence[str] | None = None,
    *,
    tail_steps: int = 0,
    audit_all: bool = False,
    workers: int = 12,
    power_policy: str = TELEMETRY_POWER_POLICY,
    soc_policy: str = TELEMETRY_SOC_POLICY,
) -> tuple[MemberData, ...]:
    """Write quality reports before failing invalid selected inputs."""
    if experiment.payload.get("schema_version") == 3:
        from Monthly_Data_1hour import load_monthly_members

        return load_monthly_members(
            experiment, requested, tail_steps=tail_steps, workers=workers,
            power_policy=power_policy,
            soc_policy=soc_policy,
        )
    names = selected_names(experiment, requested)
    paths = discover_member_paths(experiment)
    missing = set(names) - paths.keys()
    if missing:
        raise FileNotFoundError(f"missing selected members: {sorted(missing)}")
    resources = {}
    audits = []
    for name in sorted(paths if audit_all else names):
        resource, audit = inspect_member(
            paths[name], experiment, tail_steps, power_policy=power_policy,
            soc_policy=soc_policy,
        )
        audits.append(audit)
        if resource is not None:
            resources[name] = resource
        print(f"Validated {name}: {audit['reason']}", flush=True)
    report_name = (
        "selection_quality" if audit_all else "selected_quality"
    ) + f"_tail{tail_steps}"
    reports = experiment.output_root / "reports"
    atomic_json(reports / f"{report_name}_1hour.json", audits)
    pd.DataFrame(
        [
            {
                k: json.dumps(v) if isinstance(v, (dict, list)) else v
                for k, v in a.items()
            }
            for a in audits
        ]
    ).to_csv(reports / f"{report_name}_1hour.csv", index=False)
    invalid = set(names) - resources.keys()
    if invalid:
        raise ValueError(
            f"selected members failed quality checks: {sorted(invalid)}; see {reports}"
        )
    return tuple(resources[name] for name in names)


def prepare_random(experiment: Experiment, member: str) -> RandomRecord:
    """Persist one member's complete matrix once, or verify both files strictly."""
    if member not in experiment.member_seeds:
        raise ValueError(f"member has no fixed seed: {member}")
    seed = experiment.member_seeds[member]
    monthly = experiment.payload.get("schema_version") == 3
    if monthly:
        year, month = map(int, experiment.payload["month"].split("-"))
        seed = int(np.random.SeedSequence([seed, year, month]).generate_state(1)[0])
    stem = f"{member}_seed{seed}_{RANDOM_SHAPE[0]}x{RANDOM_SHAPE[1]}_1hour"
    stem = Path(experiment.output_name(stem + ".npy")).stem
    path = experiment.random_dir / f"{stem}.npy"
    manifest_path = experiment.random_dir / f"{stem}_manifest.json"
    expected = {
        "interval_minutes": 60,
        "schema_version": 3,
        "member": member,
        "seed": seed,
        "shape": list(RANDOM_SHAPE),
        "dtype": "float64",
        "generator": "numpy.random.Generator(MT19937)",
        "origin_date": experiment.origin.date().isoformat(),
        "layout": (
            "elapsed_hour_by_lookahead_lead"
            if monthly
            else "relative_day_then_hour_by_lookahead_lead"
        ),
    }
    if path.exists() != manifest_path.exists():
        raise FileNotFoundError(f"incomplete random-file pair for {member}")
    if path.exists():
        actual = json.loads(manifest_path.read_text(encoding="utf-8"))
        if any(actual.get(k) != v for k, v in expected.items()):
            raise ValueError(
                f"random manifest identity/config mismatch: {manifest_path}"
            )
        digest = sha256(path)
        if digest != actual.get("sha256"):
            raise ValueError(f"random SHA-256 mismatch: {path}")
        matrix = np.load(path, allow_pickle=False, mmap_mode="r")
        if (
            matrix.shape != RANDOM_SHAPE
            or matrix.dtype != np.dtype("float64")
            or not np.isfinite(matrix).all()
        ):
            raise ValueError(f"invalid random matrix: {path}")
    else:
        experiment.random_dir.mkdir(parents=True, exist_ok=True)
        generator = np.random.Generator(np.random.MT19937(seed))
        matrix = generator.standard_normal(RANDOM_SHAPE).astype(np.float64)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=path.parent, delete=False
            ) as handle:
                temporary = Path(handle.name)
                np.save(handle, matrix, allow_pickle=False)
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        digest = sha256(path)
        atomic_json(
            manifest_path,
            {**expected, "numpy_version": np.__version__, "sha256": digest},
        )
    return RandomRecord(member, seed, path, manifest_path, digest)


def provenance(experiment: Experiment, sources: Sequence[Path]) -> dict:
    """Read-only Git metadata plus hashes also cover untracked implementation files."""
    root = Path(__file__).resolve().parent
    code_files = [
        root / name
        for name in (
            "project_paths.py",
            "Experiment_Data_1hour.py",
            "Rolling_Window_1hour.py",
            "Benchmark_1hour.py",
            "distributed_Rolling_Window_1hour.py",
            "Monthly_Data_1hour.py",
        )
    ]

    def git(*args):
        try:
            result = subprocess.run(
                ["git", "--no-optional-locks", *args], cwd=root, capture_output=True, check=False
            )
        except FileNotFoundError:
            # Source archives can run without Git; file hashes still record provenance.
            return b"unavailable"
        return result.stdout if result.returncode == 0 else b"unavailable"

    sources = set(sources)
    if experiment.payload.get("schema_version") == 3:
        # Earlier files may supply the last valid submitted curve.
        suffix = experiment.payload["month"].replace("-", "")
        for member in experiment.member_seeds:
            sources.update(
                p
                for p in experiment.data_dir.glob(f"{member}_Usage_*.xlsx")
                if p.stem.rsplit("_", 1)[-1] < suffix
            )
    return {
        "interval_minutes": 60,
        "experiment": experiment.payload,
        "config_sha256": sha256(experiment.path),
        "python_version": platform.python_version(),
        "numpy_version": np.__version__,
        "pandas_version": pd.__version__,
        "git_head": git("rev-parse", "HEAD").decode().strip(),
        "git_diff_sha256": hashlib.sha256(git("diff", "HEAD", "--")).hexdigest(),
        "git_status": git("status", "--porcelain").decode(errors="replace"),
        "code_sha256": {p.name: sha256(p) for p in code_files if p.exists()},
        "input_sha256": {str(p.resolve()): sha256(p) for p in sorted(sources)},
    }


def validate_result_frame(
    frame: pd.DataFrame,
    method: str,
    *,
    expected_start: pd.Timestamp | None = None,
    expected_steps: int | None = None,
    efficiency: float = 0.95,
) -> None:
    """Check hourly participation, physical bounds, SOC continuity and settlement."""
    if frame.empty or (expected_steps is not None and len(frame) != expected_steps):
        raise ValueError("wrong or empty implementation result length")
    submitted_policy = False
    if POWER_POLICY in frame:
        policies = frame[POWER_POLICY]
        if not policies.eq(SUBMITTED_POWER_POLICY).all() or method != "Rolling":
            raise ValueError("invalid submitted power policy in result")
        submitted_policy = True
    soc_policy = TELEMETRY_SOC_POLICY
    if SOC_POLICY_COLUMN in frame:
        soc_policy = frame[SOC_POLICY_COLUMN].iloc[0]
        if (
            soc_policy not in (TELEMETRY_SOC_POLICY, *SUBMITTED_SOC_POLICIES)
            or not frame[SOC_POLICY_COLUMN].eq(soc_policy).all()
        ):
            raise ValueError("invalid or changing SOC policy in result")
    submitted_soc = soc_policy in SUBMITTED_SOC_POLICIES
    monthly = "Hour Start UTC" in frame
    if submitted_soc and not monthly:
        raise ValueError("submitted SOC result requires a monthly UTC timeline")
    if monthly:
        utc = pd.DatetimeIndex(pd.to_datetime(frame["Hour Start UTC"], utc=True))
        times = utc.tz_convert("America/Chicago")
        local = pd.DatetimeIndex(pd.to_datetime(frame["Time"]))
        if not local.equals(times.tz_localize(None)):
            raise ValueError("local result times disagree with UTC")
        if expected_start is not None and expected_start.tzinfo is None:
            expected_start = expected_start.tz_localize("America/Chicago")
    else:
        times = pd.DatetimeIndex(pd.to_datetime(frame["Time"]))
    start = times[0] if expected_start is None else expected_start
    if not times.equals(pd.date_range(start, periods=len(frame), freq="h")):
        raise ValueError("result timeline is not the expected hourly grid")
    if frame["Resource Name"].nunique() != 1:
        raise ValueError("result contains multiple members")
    col = "Cleared MW" if method == "Rolling" else "Executed MW"
    columns = [
        col,
        "Modeled SOC Before",
        "Modeled SOC After",
        "Applied Minimum SOC",
        "Applied Maximum SOC",
        "LMP",
        "Profit",
        EFFECTIVE_LSL,
        EFFECTIVE_HSL,
        CAPACITY,
        INITIAL_ENERGY,
        "Normalized SOC Before",
        "Normalized SOC After",
    ]
    values = frame[columns].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("non-finite modeled result")
    (
        power,
        before,
        after,
        lower,
        upper,
        price,
        profit,
        low,
        high,
        capacity,
        initial,
        norm_before,
        norm_after,
    ) = values.T
    booleans = {}
    for key in (PARTICIPATING, ZERO_DAY, AVAILABLE):
        parsed = frame[key].astype(str).str.lower().map({"true": True, "false": False})
        if parsed.isna().any():
            raise ValueError(f"invalid boolean: {key}")
        booleans[key] = parsed.to_numpy(dtype=bool)
    expected_participating = np.array(
        [
            any(json.loads(v).get(k, 0) > 0 for k in ("ON", "ONTEST"))
            for v in frame["Source Status Counts"]
        ]
    )
    if not np.array_equal(booleans[PARTICIPATING], expected_participating):
        raise ValueError("hourly participation disagrees with source statuses")
    available = booleans[PARTICIPATING] & ~booleans[ZERO_DAY]
    if not np.array_equal(available, booleans[AVAILABLE]):
        raise ValueError(
            "hourly trading gate disagrees with participation/day-zero policy"
        )
    for rating_col, bound, sign in (
        (RATED_CHARGE, low, -1),
        (RATED_DISCHARGE, high, 1),
    ):
        rating = frame[rating_col].to_numpy(dtype=float)
        if (
            not np.isfinite(rating).all()
            or np.any(rating < 0)
            or not np.all(rating == rating[0])
        ):
            raise ValueError("invalid or changing monthly rated power")
        if not np.array_equal(bound, np.where(available, rating * sign, 0.0)):
            raise ValueError(
                "effective power limits disagree with hourly gates and ratings"
            )
    for day, indices in pd.Series(np.arange(len(times)), index=times).groupby(
        times.normalize()
    ):
        day_hours = int(((day + pd.DateOffset(days=1)) - day) / pd.Timedelta(hours=1))
        if len(indices) == day_hours:
            positions = indices.to_numpy()
            expected_zero = bool(frame[TELEMETRY].iloc[positions].eq(0).all())
            if np.any(booleans[ZERO_DAY][positions] != expected_zero):
                raise ValueError("day-zero gate disagrees with hourly telemetry")
    off = ~available
    if any(np.any(a[off] != 0) for a in (power, profit, low, high)) or np.any(
        before[off] != after[off]
    ):
        raise ValueError("unavailable result traded or changed energy")
    if (
        np.any(low > 0)
        or np.any(high < 0)
        or np.any(capacity < 0)
        or np.any(lower < 0)
        or np.any(lower > upper)
        or ((not submitted_soc or soc_policy == ZERO_MIN_SOC_POLICY) and np.any(lower != 0))
        or not np.array_equal(upper, capacity)
    ):
        raise ValueError("invalid hourly power/capacity bounds")
    if (
        not np.all(capacity == capacity[0])
        or not np.all(lower == lower[0])
        or not np.all(initial == initial[0])
        or not np.isclose(before[0], initial[0], atol=1e-5, rtol=0)
    ):
        raise ValueError("monthly capacity or initial energy is inconsistent")
    if submitted_soc or USABLE_CAPACITY in frame:
        usable = frame[USABLE_CAPACITY].to_numpy(dtype=float)
        if not np.isfinite(usable).all() or not np.allclose(
            usable, upper - lower, atol=1e-8, rtol=0
        ):
            raise ValueError("usable capacity does not reconcile with SOC bounds")
    if submitted_soc:
        observed_initial = float(frame["Observed SOC"].iloc[0])
        if not math.isfinite(observed_initial) or not np.isclose(
            initial[0], observed_initial, atol=1e-8, rtol=0
        ):
            raise ValueError("initial energy disagrees with first observed SOC")
    if not np.allclose(before[1:], after[:-1], atol=1e-5, rtol=0):
        raise ValueError("SOC is not continuous")
    expected_after = (
        before
        - np.where(power >= 0, power / efficiency, power * efficiency) * INTERVAL_HOURS
    )
    energy_atol = 1e-5
    if submitted_policy:
        # Excel's decimal serialization can move a boundary residual by a few ULPs.
        # Preserve the physical tolerance; allow only floating-point roundoff on top.
        scale = np.maximum(1.0, np.maximum(np.abs(before), np.abs(after)))
        energy_atol = energy_atol + 16 * np.finfo(float).eps * scale
    if not np.allclose(after, expected_after, atol=energy_atol, rtol=0):
        raise ValueError("SOC transition does not conserve energy")
    for energy, normalized in ((before, norm_before), (after, norm_after)):
        if np.any(energy < lower - 1e-5) or np.any(energy > upper + 1e-5):
            raise ValueError("SOC outside capacity bounds")
        expected_soc = np.divide(
            energy, capacity, out=np.zeros_like(energy), where=capacity > 0
        )
        if not np.allclose(normalized, expected_soc, atol=1e-8, rtol=0):
            raise ValueError("normalized SOC does not reconcile")
    if np.any(power < low - 1e-3) or np.any(power > high + 1e-3):
        raise ValueError("power outside effective limits")
    if not np.allclose(profit, price * power * INTERVAL_HOURS, atol=1e-7, rtol=1e-10):
        raise ValueError("profit does not reconcile with executed power")
    if (
        times[0].day == 1
        and times[0].hour == 0
        and times[-1] + pd.Timedelta(hours=1) == times[0] + pd.offsets.MonthBegin(1)
    ):
        if submitted_soc:
            parameters = submitted_soc_parameters(
                frame[["Observed SOC", "Observed Minimum SOC", "Observed Maximum SOC"]]
                .rename(columns={
                    "Observed SOC": "State of Charge",
                    "Observed Minimum SOC": "Minimum SOC",
                    "Observed Maximum SOC": "Maximum SOC",
                }),
                soc_policy=soc_policy,
            )
            if not np.isclose(lower[0], parameters["minimum_energy_mwh"], atol=1e-8, rtol=0):
                raise ValueError("monthly minimum SOC does not reconcile")
            power_samples = frame[TELEMETRY].to_numpy(dtype=float)
            parameters.update(
                rated_charge_mw=ceil_tenth(max(0.0, -float(power_samples.min()))),
                rated_discharge_mw=ceil_tenth(max(0.0, float(power_samples.max()))),
            )
        else:
            parameters = reconstruct_ess(frame[TELEMETRY].to_numpy(), efficiency)
        if submitted_policy:
            parameters.update(submitted_power_parameters(
                frame[[SUBMITTED_HIGH, SUBMITTED_LOW]].rename(
                    columns={SUBMITTED_HIGH: "HSL", SUBMITTED_LOW: "LSL"}
                ), SUBMITTED_POWER_POLICY
            ))
        for column, key in (
            (CAPACITY, "capacity_mwh"),
            (INITIAL_ENERGY, "initial_energy_mwh"),
            (RATED_CHARGE, "rated_charge_mw"),
            (RATED_DISCHARGE, "rated_discharge_mw"),
        ):
            if not np.isclose(
                float(frame[column].iloc[0]), parameters[key], atol=1e-7, rtol=1e-10
            ):
                raise ValueError(
                    f"monthly parameter does not reconcile: {column}"
                )


def verify_submitted_soc_source(
    frame: pd.DataFrame, experiment: Experiment, member: str
) -> None:
    """Independently reconcile a new-policy result against its original workbook."""
    if not frame["Resource Name"].eq(member).all():
        raise ValueError(f"{member}: result resource identity disagrees with filename")
    month = experiment.payload["month"].replace("-", "")
    path = experiment.data_dir / f"{member}_Usage_{month}.xlsx"
    columns = {"Resource Name", "SCED Timestamp UTC", "State of Charge", "Minimum SOC", "Maximum SOC"}
    raw = pd.read_excel(
        path, sheet_name="Usage", keep_default_na=False,
        usecols=lambda column: column in columns,
    )
    if set(raw) != columns or not raw["Resource Name"].eq(member).all():
        raise ValueError(f"{path}: invalid SOC source columns or resource identity")
    raw["SCED Timestamp UTC"] = pd.to_datetime(raw["SCED Timestamp UTC"], utc=True, errors="raise")
    raw = raw.loc[
        raw["SCED Timestamp UTC"].ge(experiment.start.tz_convert("UTC"))
        & raw["SCED Timestamp UTC"].lt(experiment.end_exclusive.tz_convert("UTC"))
    ].sort_values("SCED Timestamp UTC", kind="stable")
    parameters = submitted_soc_parameters(
        raw, soc_policy=experiment.payload["soc_policy"]
    )
    for column, key in (
        ("Applied Minimum SOC", "minimum_energy_mwh"),
        ("Applied Maximum SOC", "maximum_energy_mwh"),
        (CAPACITY, "capacity_mwh"),
        (USABLE_CAPACITY, "usable_capacity_mwh"),
        (INITIAL_ENERGY, "initial_energy_mwh"),
    ):
        if not np.allclose(frame[column].to_numpy(dtype=float), parameters[key], atol=1e-8, rtol=0):
            raise ValueError(f"{member}: {column} disagrees with original monthly SOC source")


def summarize_frame(
    frame: pd.DataFrame, method: str, *, seed: int | None = None
) -> dict:
    validate_result_frame(frame, method)
    initial = float(frame["Modeled SOC Before"].iloc[0])
    final = float(frame["Modeled SOC After"].iloc[-1])
    participating = frame[PARTICIPATING].astype(str).str.lower().eq("true")
    available = frame[AVAILABLE].astype(str).str.lower().eq("true")
    counts = {}
    for cell in frame["Source Status Counts"]:
        for status, count in json.loads(cell).items():
            counts[status] = counts.get(status, 0) + count
    return {
        "member": str(frame["Resource Name"].iloc[0]),
        "method": method,
        "steps": len(frame),
        "interval_minutes": 60,
        "total_profit": float(frame["Profit"].sum()),
        "terminal_soc_value": final * float(frame["LMP"].iloc[-1]),
        "total_profit_with_terminal_soc": (
            float(frame["Profit"].sum()) + final * float(frame["LMP"].iloc[-1])
        ),
        "soc_policy": (
            str(frame[SOC_POLICY_COLUMN].iloc[0])
            if SOC_POLICY_COLUMN in frame else TELEMETRY_SOC_POLICY
        ),
        "minimum_energy_mwh": float(frame["Applied Minimum SOC"].iloc[0]),
        "maximum_energy_mwh": float(frame["Applied Maximum SOC"].iloc[0]),
        "usable_capacity_mwh": float(
            frame["Applied Maximum SOC"].iloc[0] - frame["Applied Minimum SOC"].iloc[0]
        ),
        "capacity_mwh": float(frame[CAPACITY].iloc[0]),
        "rated_charge_mw": float(frame[RATED_CHARGE].iloc[0]),
        "rated_discharge_mw": float(frame[RATED_DISCHARGE].iloc[0]),
        "initial_energy": initial,
        "final_energy": final,
        "energy_change": final - initial,
        "participating_ratio": float(participating.mean()),
        "available_ratio": float(available.mean()),
        "unavailable_hours": int((~available).sum()),
        "status_counts": json.dumps(counts),
        "seed": seed,
    }


def compare_completed_runs(experiment: Experiment) -> Path:
    """Compare only complete, matching full-window runs; never partial smoke output."""
    root = experiment.output_root
    rolling_manifest = json.loads(
        (root / "rolling" / experiment.output_name("run_manifest.json")).read_text(
            encoding="utf-8"
        )
    )
    benchmark_manifest = json.loads(
        (root / "benchmark" / experiment.output_name("run_manifest.json")).read_text(
            encoding="utf-8"
        )
    )
    expected_names = set(experiment.member_seeds)
    if experiment.payload.get("schema_version") == 3:
        benchmark_names = set(benchmark_manifest.get("processed_resources", []))
        rolling_names = set(rolling_manifest.get("processed_resources", []))
        benchmark_skipped = set(benchmark_manifest.get("skipped_coverage", []))
        rolling_skipped = set(rolling_manifest.get("skipped_coverage", []))
        if (
            benchmark_names != rolling_names
            or benchmark_skipped != rolling_skipped
            or benchmark_names & benchmark_skipped
            or benchmark_names | benchmark_skipped != expected_names
        ):
            raise ValueError("monthly strategy member selections or exclusions differ")
        expected_names = benchmark_names
    for manifest in (rolling_manifest, benchmark_manifest):
        if manifest["status"] != "complete" or manifest["provenance"][
            "config_sha256"
        ] != sha256(experiment.path):
            raise ValueError("comparison requires complete runs of the same experiment")
    if (
        rolling_manifest["provenance"]["input_sha256"]
        != benchmark_manifest["provenance"]["input_sha256"]
    ):
        raise ValueError("comparison source inputs differ")
    rolling = pd.read_csv(
        root / "rolling" / experiment.output_name("member_summary_1hour.csv")
    )
    benchmark = pd.read_csv(
        root / "benchmark" / experiment.output_name("member_summary_1hour.csv")
    )
    combined = pd.concat([rolling, benchmark], ignore_index=True)
    for method in ("Rolling", "Piecewise", "Discrete"):
        rows = combined.loc[combined["method"].eq(method)]
        if (
            len(rows) != len(expected_names)
            or set(rows["member"]) != expected_names
            or not rows["steps"].eq(experiment.steps).all()
        ):
            raise ValueError(f"incomplete full-window results for {method}")
    for member, rows in combined.groupby("member"):
        for column in (
            "capacity_mwh",
            "initial_energy",
            "rated_charge_mw",
            "rated_discharge_mw",
            "participating_ratio",
            "available_ratio",
            "unavailable_hours",
        ):
            values = rows[column].to_numpy(dtype=float)
            if not np.allclose(values, values[0], atol=1e-8, rtol=1e-10):
                raise ValueError(f"strategy input mismatch for {member}: {column}")
    table = combined.pivot(
        index="member", columns="method", values=["total_profit", "final_energy"]
    )
    table.columns = [f"{method}_{metric}" for metric, method in table.columns]
    for method in ("Piecewise", "Discrete"):
        table[f"Rolling_minus_{method}"] = (
            table["Rolling_total_profit"] - table[f"{method}_total_profit"]
        )
    identity = rolling.set_index("member")[
        [
            "initial_energy",
            "capacity_mwh",
            "participating_ratio",
            "available_ratio",
            "unavailable_hours",
            "status_counts",
            "seed",
            "random_path",
            "random_sha256",
        ]
    ]
    table = table.join(identity)
    reports = root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    output = reports / experiment.output_name("strategy_comparison_1hour.csv")
    table.to_csv(output)
    combined.to_csv(
        reports / experiment.output_name("all_strategy_summaries_1hour.csv"),
        index=False,
    )
    return output


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--compare", action="store_true")
    parser.add_argument("--verify-results", choices=("benchmark", "rolling"))
    parser.add_argument("--month", help="YYYY-MM; omit for all configured months")
    args = parser.parse_args()
    for month in experiment_months(args.experiment, args.month):
        experiment = load_experiment(args.experiment, month)
        if args.verify_results:
            folder = experiment.output_root / args.verify_results
            rows = []
            manifest = json.loads(
                (folder / experiment.output_name("run_manifest.json")).read_text(
                    encoding="utf-8"
                )
            )
            if manifest.get("status") != "complete":
                raise ValueError("cannot verify an incomplete run")
            names = manifest.get("processed_resources", list(experiment.member_seeds))
            new_rolling = (
                args.verify_results == "rolling"
                and experiment.payload["soc_policy"] in SUBMITTED_SOC_POLICIES
            )
            if new_rolling:
                if manifest.get("soc_policy") != experiment.payload["soc_policy"]:
                    raise ValueError("Rolling manifest uses a different SOC policy")
                if len(names) != len(experiment.member_seeds) or set(names) != set(experiment.member_seeds):
                    raise ValueError("full Rolling verification requires every configured member")
            for member in names:
                methods = (
                    ("Rolling",)
                    if args.verify_results == "rolling"
                    else ("Piecewise", "Discrete")
                )
                for method in methods:
                    name = (
                        f"{member}_Rolling_Window_1hour.xlsx"
                        if method == "Rolling"
                        else f"{member}_Profit_{method}_1hour.xlsx"
                    )
                    path = folder / experiment.output_name(name)
                    frame = pd.read_excel(path, keep_default_na=False)
                    validate_result_frame(
                        frame,
                        method,
                        expected_start=experiment.start,
                        expected_steps=experiment.steps,
                    )
                    if new_rolling:
                        if SOC_POLICY_COLUMN not in frame or not frame[SOC_POLICY_COLUMN].eq(experiment.payload["soc_policy"]).all():
                            raise ValueError("Rolling result uses a different SOC policy")
                        verify_submitted_soc_source(frame, experiment, member)
                    rows.append(
                        {
                            "member": member,
                            "method": method,
                            "steps": len(frame),
                            "verified": True,
                        }
                    )
                    print(
                        f"Verified {member} / {method}: {len(frame)} steps", flush=True
                    )
            atomic_json(
                experiment.output_root
                / "reports"
                / experiment.output_name(
                    f"{args.verify_results}_verification_1hour.json"
                ),
                rows,
            )
        elif args.compare:
            print(compare_completed_runs(experiment))
        else:
            load_members(
                experiment, audit_all=True,
                soc_policy=experiment.payload["soc_policy"],
            )
