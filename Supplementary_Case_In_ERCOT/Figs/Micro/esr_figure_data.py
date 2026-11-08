"""Read one ERCOT ESR month and derive the shared a/b/c figure statistics.

Inputs are classified Rolling worksheets, with explicit UTC and local hours.
State counts use the existing classifier's zero-limit policy. Prices retain
every physical hour. Widths reproduce the legacy price-span calculation,
testing current segment energies against a boundary band in MWh.
"""

from __future__ import annotations

import argparse
import calendar
import json
import math
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import load_workbook
from pytz.exceptions import InvalidTimeError
from scipy.stats import t


MICRO_FIGS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MICRO_FIGS_DIR.parents[1]
PROGRAM_DIR = PROJECT_ROOT / "Program" / "Micro"
if str(PROGRAM_DIR) not in sys.path:
    sys.path.insert(0, str(PROGRAM_DIR))

from classify_p_ess import (  # noqa: E402
    classify_p_ess,
    parse_dsfunction,
    row_has_zero_power_limit,
    values_match,
)


DEFAULT_MONTH = "202605"
TIMEZONE = "America/Chicago"
PNG_DPI = 720
CONFIDENCE_LEVEL = 0.90
# Keep the archive range check separate from the MWh classification band.
SOC_RANGE_TOLERANCE = 1e-5
MB_ENERGY_TOLERANCE_MWH = 1e-3
STATE_ORDER = ("MC", "CC", "CD", "NU", "DC", "DD", "MD")
WIDTH_STATE_ORDER = ("CC", "CD", "NU", "DC", "DD")
COLORS = {
    "MC": "#C90000",
    "CC": "#FF6B6B",
    "CD": "#F6B4B4",
    "NU": "#A6A6A6",
    "DC": "#9CC5E5",
    "DD": "#4F95D5",
    "MD": "#1D4B73",
}
LEGEND_LABELS = {
    "MC": "Max-rate charge",
    "CC": "Charge-for-charge",
    "CD": "Charge-for-discharge",
    "NU": "Null segment",
    "DC": "Discharge-for-charge",
    "DD": "Discharge-for-discharge",
    "MD": "Max-rate discharge",
}
CONFIG_PATH = PROGRAM_DIR / "experiment_config_1hour.json"
CLASSIFIED_DIR = PROJECT_ROOT / "Results" / "Micro_Results" / "Classified"
HUB_PRICE_DIR = PROJECT_ROOT / "Data" / "Macro" / "Price"
OUTPUT_ROOT = MICRO_FIGS_DIR / "ESR_ABC"
REQUIRED_HEADERS = (
    "Time",
    "Hour Start UTC",
    "Resource Name",
    "Price Source",
    "LMP",
    "Pcmax",
    "Pdmax",
    "P_ESS_state",
    "dsfunction",
    "Applied Minimum SOC",
    "Applied Maximum SOC",
)


@dataclass(frozen=True)
class StateCountData:
    """Seven clock-hour state counts and their reconciled audit counts."""

    counts: dict[str, list[int]]
    valid: list[int]
    excluded_zero_limit: list[int]
    excluded_nc: list[int]
    raw: list[int]


@dataclass(frozen=True)
class HourlyPriceSummary:
    """Mean hourly price and two-sided 90% Student-t interval for one hour."""

    hour: int
    mean: float
    ci_low: float
    ci_high: float
    standard_deviation: float
    sample_size: int


@dataclass(frozen=True)
class HubPriceData:
    """Validated Hub month with one price sample per local date and clock hour."""

    month: str
    source_path: Path
    prices: list[HourlyPriceSummary]
    physical_hours: int


@dataclass(frozen=True)
class ESRFigureData:
    """Validated source and common statistics for one member and month."""

    resource: str
    month: str
    source_path: Path
    width_sums: dict[str, float]
    state_counts: Counter[str]
    hourly_states: StateCountData
    prices: list[HourlyPriceSummary]
    raw_rows: int
    excluded_zero_limit: int

    @property
    def valid_rows(self) -> int:
        return sum(self.state_counts.values())


# Input contracts
def configured_resources() -> tuple[str, ...]:
    """Return the ordered, filename-safe ESR membership from the experiment."""
    with CONFIG_PATH.open(encoding="utf-8") as handle:
        members = json.load(handle).get("members")
    if not isinstance(members, dict) or not members:
        raise ValueError(f"{CONFIG_PATH}: members must be a nonempty object.")
    for name in members:
        if (
            not isinstance(name, str)
            or not name
            or any(not (character.isalnum() or character in "_-") for character in name)
        ):
            raise ValueError(f"{CONFIG_PATH}: unsafe resource name {name!r}.")
    return tuple(members)


def select_resources(resource: str | None) -> tuple[str, ...]:
    """Select all configured members, or validate a requested single member."""
    members = configured_resources()
    if resource is None:
        return members
    if resource not in members:
        raise ValueError(f"Unknown ESR {resource!r}; choose from {', '.join(members)}.")
    return (resource,)


def _month_hours(month: str) -> pd.DatetimeIndex:
    if (
        not isinstance(month, str)
        or len(month) != 6
        or not month.isascii()
        or not month.isdigit()
        or int(month[:4]) == 0
    ):
        raise ValueError(f"Month must be YYYYMM; found {month!r}.")
    try:
        start = pd.Timestamp(f"{month[:4]}-{month[4:]}-01", tz=TIMEZONE)
        end = start + pd.offsets.MonthBegin(1)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"Invalid month {month!r}.") from exc
    return pd.date_range(start, end, freq="h", inclusive="left").tz_convert("UTC")


def _number(value: object, context: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{context}: expected a finite number, found {value!r}.")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(
            f"{context}: expected a finite number, found {value!r}."
        ) from exc
    if not math.isfinite(result):
        raise ValueError(f"{context}: expected a finite number, found {value!r}.")
    return result


def _timestamp(value: object, context: str) -> pd.Timestamp:
    if not isinstance(value, (str, pd.Timestamp)) and not hasattr(value, "year"):
        raise ValueError(f"{context}: invalid datetime {value!r}.")
    try:
        timestamp = pd.Timestamp(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{context}: invalid datetime {value!r}.") from exc
    if pd.isna(timestamp):
        raise ValueError(f"{context}: datetime is missing.")
    return timestamp


# Scientific derivations
def segment_widths(
    dsfunction_value: object,
    *,
    pcmax: float,
    pdmax: float,
    maximum_soc: float,
    context: str = "dsfunction",
) -> dict[str, float]:
    """Reconstruct five price-width totals, excluding max-rate and SOC boundaries.

    Segment categories and their priority are delegated to classify_p_ess.
    Zero-width sampled first segments carry no area but do not remove an hour.
    The current energy contract has minimum SOC zero and capacity maximum_soc.
    """
    capacity = _number(maximum_soc, f"{context}, maximum SOC")
    if capacity <= 0:
        raise ValueError(f"{context}: maximum SOC must be positive.")
    pcmax = _number(pcmax, f"{context}, Pcmax")
    pdmax = _number(pdmax, f"{context}, Pdmax")
    try:
        segments = parse_dsfunction(dsfunction_value, 0)
    except ValueError as exc:
        raise ValueError(f"{context}: {exc}") from exc
    widths = dict.fromkeys(WIDTH_STATE_ORDER, 0.0)
    previous_upper = None
    for index, segment in enumerate(segments, start=1):
        segment_context = f"{context}, segment {index}"
        if len(segment) != 8:
            raise ValueError(
                f"{segment_context}: expected eight values including energy."
            )
        values = [
            _number(value, f"{segment_context}, column {column}")
            for column, value in enumerate(segment, start=1)
        ]
        lower, upper, power = values[:3]
        energy = values[7]
        width = upper - lower
        if width < 0:
            raise ValueError(
                f"{segment_context}: negative price interval width {width}."
            )
        if previous_upper is not None and not math.isclose(
            lower, previous_upper, rel_tol=1e-9, abs_tol=1e-8
        ):
            raise ValueError(f"{segment_context}: price intervals are not contiguous.")
        previous_upper = upper
        soc = energy / capacity
        if soc < -SOC_RANGE_TOLERANCE or soc > 1 + SOC_RANGE_TOLERANCE:
            raise ValueError(
                f"{segment_context}: normalized energy {soc} is outside [0, 1]."
            )
        # Boundary occupancy takes priority even when dispatch is zero or inward.
        if (
            energy <= MB_ENERGY_TOLERANCE_MWH
            or energy >= capacity - MB_ENERGY_TOLERANCE_MWH
        ):
            continue
        state = classify_p_ess(power, values, 0, pcmax=pcmax, pdmax=pdmax)
        if state == "NC":
            raise ValueError(f"{segment_context}: segment is not classified.")
        if state in widths:
            widths[state] += width
    return widths


def summarize_prices(
    prices_by_hour: dict[int, list[float]],
) -> list[HourlyPriceSummary]:
    """Pool daily hourly price samples; use sample SD and actual n for the CI."""
    if set(prices_by_hour) != set(range(24)):
        raise ValueError("Price samples must cover clock hours 0 through 23 exactly.")
    summaries = []
    for hour in range(24):
        samples = np.asarray(
            [_number(value, f"Hour {hour:02d} LMP") for value in prices_by_hour[hour]],
            dtype=float,
        )
        if len(samples) < 2:
            raise ValueError(
                f"Hour {hour:02d}: at least two price samples are required."
            )
        mean = float(samples.mean())
        standard_deviation = float(samples.std(ddof=1))
        critical = float(t.ppf((1 + CONFIDENCE_LEVEL) / 2, len(samples) - 1))
        margin = critical * standard_deviation / math.sqrt(len(samples))
        summary = HourlyPriceSummary(
            hour, mean, mean - margin, mean + margin, standard_deviation, len(samples)
        )
        if not all(
            math.isfinite(value) for value in (mean, summary.ci_low, summary.ci_high)
        ):
            raise ValueError(f"Hour {hour:02d}: price statistics are not finite.")
        summaries.append(summary)
    return summaries


def load_hub_price_data(
    month: str = DEFAULT_MONTH, price_dir: Path | None = None
) -> HubPriceData:
    """Validate physical month coverage, then summarize daily Hub clock hours.

    Next-month forecast tails are ignored. Autumn's two physical occurrences
    of a local clock hour require UTC timestamps and become one daily mean;
    spring's absent clock hour contributes no invented sample.
    """
    expected = _month_hours(month)
    directory = HUB_PRICE_DIR if price_dir is None else Path(price_dir)
    source_path = directory / f"Price_{calendar.month_name[int(month[4:])]}.xlsx"
    start = expected[0].tz_convert(TIMEZONE).tz_localize(None)
    end = start + pd.offsets.MonthBegin(1)
    daily_prices: dict[tuple[date, int], list[float]] = {}
    row_count = 0
    workbook = load_workbook(source_path, read_only=True, data_only=True)
    try:
        if "Price" not in workbook.sheetnames:
            raise ValueError(f"{source_path.name}: Price worksheet is missing.")
        rows = workbook["Price"].iter_rows(values_only=True)
        headers = next(rows, ())
        required = ("Interval Start", "Official_Price")
        for name in required:
            if headers.count(name) != 1:
                raise ValueError(f"{source_path.name}: header {name!r} must occur once.")
        if headers.count("Hour Start UTC") > 1:
            raise ValueError(
                f"{source_path.name}: header 'Hour Start UTC' must occur at most once."
            )
        columns = {name: headers.index(name) for name in required}
        has_utc = "Hour Start UTC" in headers
        if has_utc:
            columns["Hour Start UTC"] = headers.index("Hour Start UTC")
        for excel_row, row in enumerate(rows, start=2):
            if all(value is None for value in row):
                continue
            context = f"{source_path.name}, row {excel_row}"
            local = _timestamp(row[columns["Interval Start"]], f"{context}, local time")
            if local.tzinfo is not None:
                raise ValueError(f"{context}: Interval Start must be naive ERCOT local time.")
            if not start <= local < end:
                continue
            if has_utc:
                utc = _timestamp(row[columns["Hour Start UTC"]], f"{context}, UTC")
                utc = utc.tz_localize("UTC") if utc.tzinfo is None else utc.tz_convert("UTC")
                if local != utc.tz_convert(TIMEZONE).tz_localize(None):
                    raise ValueError(f"{context}: local time disagrees with Hour Start UTC.")
            else:
                try:
                    utc = local.tz_localize(
                        TIMEZONE, ambiguous="raise", nonexistent="raise"
                    ).tz_convert("UTC")
                except (ValueError, TypeError, InvalidTimeError) as exc:
                    raise ValueError(
                        f"{context}: ambiguous or nonexistent ERCOT local hour; "
                        "supply Hour Start UTC to identify physical hours."
                    ) from exc
            if row_count >= len(expected) or utc != expected[row_count]:
                raise ValueError(
                    f"{context}: unexpected, duplicated or missing UTC hour {utc}."
                )
            price = _number(row[columns["Official_Price"]], f"{context}, Official_Price")
            daily_prices.setdefault((local.date(), local.hour), []).append(price)
            row_count += 1
    finally:
        workbook.close()
    if row_count != len(expected):
        raise ValueError(
            f"{source_path.name}: expected {len(expected)} hours, found {row_count}."
        )
    prices_by_hour = {hour: [] for hour in range(24)}
    for (_, hour), samples in daily_prices.items():
        prices_by_hour[hour].append(sum(samples) / len(samples))
    return HubPriceData(month, source_path, summarize_prices(prices_by_hour), row_count)


def load_month_data(resource: str, month: str = DEFAULT_MONTH) -> ESRFigureData:
    """Validate and derive a member's full calendar month without input edits."""
    select_resources(resource)
    expected = _month_hours(month)
    source_path = CLASSIFIED_DIR / f"classified_{resource}_1hour_{month}.xlsx"
    counts = {state: [0] * 24 for state in STATE_ORDER}
    raw = [0] * 24
    excluded = [0] * 24
    prices_by_hour = {hour: [] for hour in range(24)}
    widths = dict.fromkeys(WIDTH_STATE_ORDER, 0.0)
    row_count = 0
    workbook = load_workbook(source_path, read_only=True, data_only=True)
    try:
        if "Rolling" not in workbook.sheetnames:
            raise ValueError(f"{source_path.name}: Rolling worksheet is missing.")
        rows = workbook["Rolling"].iter_rows(values_only=True)
        headers = next(rows)
        for name in REQUIRED_HEADERS:
            if headers.count(name) != 1:
                raise ValueError(
                    f"{source_path.name}: header {name!r} must occur once."
                )
        columns = {name: headers.index(name) for name in REQUIRED_HEADERS}
        for excel_row, row in enumerate(rows, start=2):
            if all(value is None for value in row):
                continue
            context = f"{source_path.name}, row {excel_row}"
            value = {name: row[index] for name, index in columns.items()}
            if value["Resource Name"] != resource:
                raise ValueError(f"{context}: Resource Name does not match {resource}.")
            if value["Price Source"] != "member_lmp":
                raise ValueError(f"{context}: price source must be member_lmp.")
            utc = _timestamp(value["Hour Start UTC"], f"{context}, UTC")
            utc = (
                utc.tz_localize("UTC") if utc.tzinfo is None else utc.tz_convert("UTC")
            )
            if row_count >= len(expected) or utc != expected[row_count]:
                raise ValueError(
                    f"{context}: unexpected, duplicated or missing UTC hour {utc}."
                )
            local = _timestamp(value["Time"], f"{context}, Time")
            if local.tzinfo is not None or local != utc.tz_convert(
                TIMEZONE
            ).tz_localize(None):
                raise ValueError(
                    f"{context}: local Time disagrees with Hour Start UTC."
                )
            row_count += 1
            hour = local.hour
            raw[hour] += 1
            prices_by_hour[hour].append(_number(value["LMP"], f"{context}, LMP"))
            pcmax = _number(value["Pcmax"], f"{context}, Pcmax")
            pdmax = _number(value["Pdmax"], f"{context}, Pdmax")
            if pcmax > 0 or pdmax < 0:
                raise ValueError(f"{context}: Pcmax/Pdmax have invalid signs.")
            state = value["P_ESS_state"]
            if row_has_zero_power_limit(pcmax, pdmax):
                if state not in (None, ""):
                    raise ValueError(f"{context}: zero-limit row has a nonblank state.")
                excluded[hour] += 1
                continue
            if state not in STATE_ORDER:
                raise ValueError(f"{context}: unknown P_ESS_state {state!r}.")
            minimum_soc = _number(
                value["Applied Minimum SOC"], f"{context}, minimum SOC"
            )
            if not values_match(minimum_soc, 0.0):
                raise ValueError(
                    f"{context}: width normalization requires minimum SOC zero."
                )
            row_widths = segment_widths(
                value["dsfunction"],
                pcmax=pcmax,
                pdmax=pdmax,
                maximum_soc=value["Applied Maximum SOC"],
                context=context,
            )
            counts[state][hour] += 1
            for name in WIDTH_STATE_ORDER:
                widths[name] += row_widths[name]
    finally:
        workbook.close()
    if row_count != len(expected):
        raise ValueError(
            f"{source_path.name}: expected {len(expected)} hours, found {row_count}."
        )
    valid = [sum(counts[state][hour] for state in STATE_ORDER) for hour in range(24)]
    if any(valid[hour] + excluded[hour] != raw[hour] for hour in range(24)):
        raise ValueError(f"{source_path.name}: state counts do not reconcile.")
    state_counts = Counter({state: sum(counts[state]) for state in STATE_ORDER})
    if not sum(state_counts.values()) or not sum(widths.values()) > 0:
        raise ValueError(f"{source_path.name}: no plotted states or five-state widths.")
    prices = summarize_prices(prices_by_hour)
    expected_hour_counts = Counter(expected.tz_convert(TIMEZONE).hour)
    if any(
        summary.sample_size != expected_hour_counts[summary.hour] for summary in prices
    ):
        raise ValueError(
            f"{source_path.name}: price sample counts disagree with calendar."
        )
    return ESRFigureData(
        resource,
        month,
        source_path,
        widths,
        state_counts,
        StateCountData(counts, valid, excluded, [0] * 24, raw),
        prices,
        row_count,
        sum(excluded),
    )


# CLI and figure assembly
def calendar_month(value: str) -> str:
    """Validate the calendar window before loading or exporting any resources."""
    try:
        _month_hours(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return value


def create_argument_parser(
    description: str,
    *,
    allow_resource: bool = True,
    resource_help: str = "Draw one ESR.",
) -> argparse.ArgumentParser:
    """Use one month/member CLI contract across batch and individual panels."""
    parser = argparse.ArgumentParser(description=description)
    if allow_resource:
        parser.add_argument(
            "--resource", choices=configured_resources(), help=resource_help
        )
    parser.add_argument(
        "--month",
        type=calendar_month,
        default=DEFAULT_MONTH,
        help="Calendar month YYYYMM (default: 202605).",
    )
    return parser


def save_png(figure, path: Path, title: str) -> Path:
    """Export a fixed-canvas, transparent PNG with 720 dpi metadata."""
    if path.suffix.lower() != ".png":
        raise ValueError(f"PNG output required; found {path}.")
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=PNG_DPI, transparent=True, metadata={"Title": title})
    return path
