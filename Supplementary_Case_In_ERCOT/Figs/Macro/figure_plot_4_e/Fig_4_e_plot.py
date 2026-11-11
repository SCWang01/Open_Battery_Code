"""Plot aggregate ESR charging shifts, curtailment proxy and hourly Hub price.

The default is the complete ERCOT local day 2026-04-07. Source values retain
their hourly MW and USD/MWh units. The same 13 ESRs define Actual and Rolling;
positive charging shift means Rolling charged more or discharged less.
Only a transparent 720 dpi PNG is exported. No optimization is performed.
"""

from __future__ import annotations

import argparse
import calendar
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re

import matplotlib

matplotlib.use("Agg")

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from openpyxl import load_workbook
from pytz.exceptions import InvalidTimeError


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATE = "2026-04-07"
OUTPUT_PATH = BASE_DIR / "Figs" / f"Fig_4_e_{DEFAULT_DATE.replace('-', '')}.png"
LOCAL_ZONE = "America/Chicago"
LOCAL_TIME = "Interval Start"
UTC_TIME = "Hour Start UTC"
N_HOURS = 24
PLOT_HOURS = np.arange(N_HOURS)
FIGURE_WIDTH_MM = 180.0
FIGURE_HEIGHT_MM = 64.0
PNG_DPI = 720
NUMERIC_ATOL = 1e-7
NUMERIC_RTOL = 1e-12
POWER_LIMITS = (-1000.0, 5000.0)
PRICE_LIMITS = (0.0, 70.0)
CURTAILMENT_COLUMN = "Total Curtailment Proxy (MW)"


@dataclass(frozen=True)
class DayFigureData:
    """Aligned source values for exactly 24 consecutive physical UTC hours."""

    date: str
    local_hours: pd.DatetimeIndex
    utc_hours: pd.DatetimeIndex
    charging_shift_mw: np.ndarray
    curtailment_proxy_mw: np.ndarray
    price_usd_per_mwh: np.ndarray


# Source validation and scientific mapping
def _day_hours(date: str) -> tuple[pd.DatetimeIndex, pd.DatetimeIndex]:
    """Validate a 2026 January-June day with exactly 24 physical hours."""
    if not isinstance(date, str) or re.fullmatch(r"2026-\d{2}-\d{2}", date) is None:
        raise ValueError(
            f"Date must be YYYY-MM-DD in January-June 2026; found {date!r}"
        )
    try:
        start = pd.Timestamp(date, tz=LOCAL_ZONE)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid date {date!r}") from exc
    if start.month not in range(1, 7):
        raise ValueError(f"Date must be in January-June 2026; found {date!r}")
    end = start + pd.DateOffset(days=1)
    utc = pd.date_range(
        start.tz_convert("UTC"), end.tz_convert("UTC"), freq="h", inclusive="left"
    )
    if len(utc) != N_HOURS:
        raise ValueError(
            f"{date}: expected a 24-hour physical day, found {len(utc)}; do not pad daylight-saving hours"
        )
    return utc.tz_convert(LOCAL_ZONE).tz_localize(None), utc


def _timestamp(value: object, *, context: str, utc: bool = False) -> pd.Timestamp:
    if isinstance(value, (bool, np.bool_)) or not isinstance(
        value, (datetime, pd.Timestamp, str)
    ):
        raise ValueError(f"{context}: expected a populated datetime, found {value!r}")
    try:
        timestamp = pd.Timestamp(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{context}: invalid timestamp {value!r}") from exc
    if pd.isna(timestamp):
        raise ValueError(f"{context}: timestamp is missing")
    if utc:
        return (
            timestamp.tz_localize("UTC")
            if timestamp.tzinfo is None
            else timestamp.tz_convert("UTC")
        )
    if timestamp.tzinfo is not None:
        raise ValueError(f"{context}: expected naive ERCOT local time")
    return timestamp


def _numeric(values: list[object], *, context: str) -> np.ndarray:
    """Preserve Excel cell types until boolean and finite-number validation."""
    raw = np.asarray(values, dtype=object)
    if any(isinstance(value, (bool, np.bool_)) for value in raw):
        raise ValueError(f"{context}: boolean cells are not numeric measurements")
    try:
        numbers = raw.astype(float)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"{context}: expected finite numeric measurements") from exc
    if not np.isfinite(numbers).all():
        raise ValueError(f"{context}: expected finite numeric measurements")
    return numbers


def _read_day_table(
    path: Path,
    sheet_name: str,
    columns: tuple[str, ...],
    date: str,
    expected_local: pd.DatetimeIndex,
    expected_utc: pd.DatetimeIndex,
    *,
    require_utc: bool = False,
) -> pd.DataFrame:
    """Select a local day without sorting, filling or positional slicing."""
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        if sheet_name not in workbook.sheetnames:
            raise ValueError(f"{path}: missing worksheet {sheet_name!r}")
        rows = workbook[sheet_name].iter_rows(values_only=True)
        headers = next(rows, ())
        required = (LOCAL_TIME, *columns, *((UTC_TIME,) if require_utc else ()))
        for name in required:
            if headers.count(name) != 1:
                raise ValueError(
                    f"{path}: required column {name!r} must occur exactly once"
                )
        positions = {name: headers.index(name) for name in required}
        has_utc = headers.count(UTC_TIME) == 1
        if has_utc:
            positions[UTC_TIME] = headers.index(UTC_TIME)
        selected_rows = []
        local_values = []
        utc_values = []
        for row_number, row in enumerate(rows, start=2):
            if all(value is None for value in row):
                continue
            local = _timestamp(
                row[positions[LOCAL_TIME]],
                context=f"{path}, row {row_number}, {LOCAL_TIME}",
            )
            if local.strftime("%Y-%m-%d") != date:
                continue
            selected_rows.append(row)
            local_values.append(local)
            if has_utc:
                utc_values.append(
                    _timestamp(
                        row[positions[UTC_TIME]],
                        context=f"{path}, row {row_number}, {UTC_TIME}",
                        utc=True,
                    )
                )
        local = pd.DatetimeIndex(local_values)
        if not local.equals(expected_local):
            raise ValueError(
                f"{path}: expected exactly 24 ordered unique local hours 00:00-23:00 for {date}, found {len(local)}"
            )
        try:
            utc = (
                pd.DatetimeIndex(utc_values)
                if has_utc
                else local.tz_localize(
                    LOCAL_ZONE, ambiguous="raise", nonexistent="raise"
                ).tz_convert("UTC")
            )
        except (ValueError, TypeError, InvalidTimeError) as exc:
            raise ValueError(f"{path}: invalid local/UTC timestamps: {exc}") from exc
        if not utc.equals(expected_utc):
            raise ValueError(f"{path}: UTC hours disagree with ERCOT local day {date}")
        result = pd.DataFrame(index=expected_utc)
        for name in columns:
            result[name] = _numeric(
                [row[positions[name]] for row in selected_rows],
                context=f"{path}, {name}",
            )
        return result
    finally:
        workbook.close()


def read_day_data(
    date: str = DEFAULT_DATE, project_root: Path = PROJECT_ROOT,
    *, results_dir: Path | None = None,
) -> DayFigureData:
    """Read 13-ESR dispatch plus direct curtailment and hourly Official_Price.

    The independent Macro sources must match the system result's baseline
    curtailment and price. Official_Price is already an hourly average, so no
    daily/monthly averaging or resampling changes its 24 plotted values.
    """
    local, utc = _day_hours(date)
    root = Path(project_root)
    token = date[:7].replace("-", "")
    month_name = calendar.month_name[int(date[5:7])]
    system_dir = (
        Path(results_dir) if results_dir is not None
        else root / "Results/Macro_Results/System_Results"
    )
    system_path = system_dir / f"Rolling_Result_{token}.xlsx"
    source = _read_day_table(
        system_path,
        "Rolling",
        ("P_ESS_actual", "P_ESS", "curtailment_proxy_actual", "price"),
        date,
        local,
        utc,
        require_utc=True,
    )
    curtailment_path = (
        root / "Data" / "Macro" / "Curtailment" / f"Curtailment_{token}.xlsx"
    )
    curtailment = _read_day_table(
        curtailment_path, "Curtailment", (CURTAILMENT_COLUMN,), date, local, utc
    )
    price_path = root / "Data" / "Macro" / "Price" / f"Price_{month_name}.xlsx"
    prices = _read_day_table(price_path, "Price", ("Official_Price",), date, local, utc)
    actual_curtailment = curtailment[CURTAILMENT_COLUMN].to_numpy()
    hourly_price = prices["Official_Price"].to_numpy()
    if (actual_curtailment < 0).any():
        raise ValueError(
            f"{curtailment_path}: curtailment proxy must be nonnegative MW"
        )
    for direct, result, label in (
        (
            actual_curtailment,
            source["curtailment_proxy_actual"].to_numpy(),
            "curtailment proxy",
        ),
        (hourly_price, source["price"].to_numpy(), "Official_Price"),
    ):
        if not np.allclose(direct, result, rtol=NUMERIC_RTOL, atol=NUMERIC_ATOL):
            raise ValueError(
                f"{date}: direct {label} source disagrees with the system result"
            )
    return DayFigureData(
        date,
        local,
        utc,
        source["P_ESS_actual"].to_numpy() - source["P_ESS"].to_numpy(),
        actual_curtailment,
        hourly_price,
    )


# Figure assembly and PNG-only export
def configure_matplotlib() -> None:
    """Preserve the existing hourly figure's typography and transparent style."""
    mpl.rcParams.update(
        {
            "font.family": ["Times New Roman", "Arial", "DejaVu Serif"],
            "font.serif": ["Times New Roman", "Times", "DejaVu Serif"],
            "font.size": 9,
            "axes.labelsize": 10,
            "xtick.labelsize": 8.5,
            "ytick.labelsize": 8.5,
            "legend.fontsize": 8.5,
            "axes.linewidth": 0.7,
            "axes.unicode_minus": False,
            "hatch.linewidth": 0.55,
            "savefig.transparent": True,
        }
    )


def build_figure(data: DayFigureData) -> plt.Figure:
    """Draw validated day values on the approved fixed power and price axes."""
    for field, values, limits in (
        ("charging shift", data.charging_shift_mw, POWER_LIMITS),
        ("curtailment proxy", data.curtailment_proxy_mw, POWER_LIMITS),
        ("hourly Hub price", data.price_usd_per_mwh, PRICE_LIMITS),
    ):
        numbers = _numeric(list(values), context=f"{data.date}, {field}")
        if numbers.shape != (N_HOURS,):
            raise ValueError(f"{data.date}, {field}: expected exactly 24 hourly values")
        if (numbers < limits[0]).any() or (numbers > limits[1]).any():
            raise ValueError(
                f"{data.date}, {field}: values fall outside fixed axis limits {limits}; refusing to hide data"
            )
    configure_matplotlib()
    fig, ax_power = plt.subplots(
        figsize=(FIGURE_WIDTH_MM / 25.4, FIGURE_HEIGHT_MM / 25.4), facecolor="none"
    )
    fig.subplots_adjust(left=0.10, right=0.905, bottom=0.22, top=0.82)
    ax_price = ax_power.twinx()
    fig.patch.set_alpha(0.0)
    ax_power.patch.set_alpha(0.0)
    ax_price.patch.set_alpha(0.0)
    bar_width = 0.20
    increment_bars = ax_power.bar(
        PLOT_HOURS - bar_width / 2,
        data.charging_shift_mw,
        width=bar_width,
        color="#008000",
        label="Net shift toward charging",
    )
    curtailment_bars = ax_power.bar(
        PLOT_HOURS + bar_width / 2,
        data.curtailment_proxy_mw,
        width=bar_width,
        color="#9467bd",
        label="Curtailment proxy",
    )
    (price_line,) = ax_price.plot(
        PLOT_HOURS,
        data.price_usd_per_mwh,
        color="#ff8c00",
        marker="o",
        linewidth=0.9,
        markersize=2.1,
        markeredgewidth=0,
        label="Hub average price",
    )
    ax_power.axhline(0, color="black", linewidth=0.8)
    ax_power.set_xlabel("Time (hours)")
    ax_power.set_ylabel("Power (MW)", color="#1f4e79")
    ax_price.set_ylabel("Price (USD/MWh)")
    ax_power.set_xticks(PLOT_HOURS)
    ax_power.set_xlim(-0.4, N_HOURS - 0.6)
    ax_power.set_ylim(*POWER_LIMITS)
    ax_power.set_yticks(np.arange(-1000, 5001, 1000))
    ax_price.set_ylim(*PRICE_LIMITS)
    ax_price.set_yticks(np.arange(0, 71, 10))
    ax_power.grid(axis="y", linestyle=":", linewidth=0.6, alpha=0.45)
    fig.legend(
        [curtailment_bars, increment_bars, price_line],
        ["Curtailment proxy", "Net shift toward charging", "Hub average price"],
        loc="upper center",
        bbox_to_anchor=(0.5, 0.985),
        ncol=3,
        frameon=True,
        facecolor="none",
        edgecolor="#d9d9d9",
        framealpha=1.0,
    )
    return fig


def plot_fig_4_e(
    date: str = DEFAULT_DATE,
    output_path: Path | None = None,
    *,
    project_root: Path = PROJECT_ROOT,
    results_dir: Path | None = None,
) -> Path:
    """Export one transparent 720 dpi PNG after validating all three sources."""
    data = read_day_data(date, project_root, results_dir=results_dir)
    path = (
        Path(output_path)
        if output_path is not None
        else BASE_DIR / "Figs" / f"Fig_4_e_{date.replace('-', '')}.png"
    )
    if path.suffix.lower() != ".png":
        raise ValueError(f"Only PNG output is supported; found {path}")
    figure = build_figure(data)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(
            path,
            dpi=PNG_DPI,
            transparent=True,
            metadata={
                "Title": f"ERCOT 13-ESR charging shift, curtailment proxy and hourly Hub price, {date}"
            },
        )
    finally:
        plt.close(figure)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=DEFAULT_DATE)
    parser.add_argument(
        "--output",
        type=Path,
        help="PNG output path; default Figs/Fig_4_e_YYYYMMDD.png beside this script",
    )
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument(
        "--results-dir", type=Path,
        help="system workbook directory; use System_Results/thermal for thermal runs",
    )
    args = parser.parse_args()
    print(plot_fig_4_e(
        args.date, args.output, project_root=args.project_root, results_dir=args.results_dir
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
