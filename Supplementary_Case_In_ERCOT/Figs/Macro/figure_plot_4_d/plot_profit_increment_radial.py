"""Read 2026 ERCOT system rates and draw the shared Figure 4d radial panels.

Monthly profit retains Micro trading income plus its terminal energy value.
Cost and CO2 rates describe the selected gas, coal or thermal merit stack.
All improvements compare Rolling against Actual for the same 13 ESRs; the
six-month Overall rates use sums of the original amounts rather than means of
monthly percentages. This module reads inputs and draws axes without exports.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.textpath import TextPath
import numpy as np
from openpyxl import load_workbook


MONTHS = tuple(f"2026{month:02d}" for month in range(1, 7))
MONTH_HOURS = dict(zip(MONTHS, (744, 672, 743, 720, 744, 720)))
SCENARIOS = ("Actual", "Rolling")
BASES = ("gas", "coal", "thermal")
REQUIRED_METRICS = (
    "Month",
    "Real_Hours",
    "Control_Share",
    "Selected_ESR_Count",
    "Trading_Profit",
    "Final_SOC_Value",
    "total_profit",
    "total_carbon",
    "total_cost",
)
INNER_RADIUS = 0.15
ZERO_RADIUS = 1.0
ANNULUS_INNER_RADIUS = 1.08
ANNULUS_OUTER_RADIUS = 1.32
PLOT_LIMIT = 1.37
GRID_COLOR = "#B7B7B7"
TEXT_COLOR = "#333333"
BLUE = "#4C8FD3"
ANNULUS_BLUE = "#DCEAF7"
GREEN = "#55A868"
ANNULUS_GREEN = "#DCEFE3"
ORANGE = "#E69F45"
ANNULUS_ORANGE = "#F9E6CF"
NUMERIC_ATOL = 1e-6
NUMERIC_RTOL = 1e-10


@dataclass(frozen=True)
class MetricData:
    """Six ordered percentages and the amount-weighted half-year percentage."""

    months: tuple[str, ...]
    monthly_percent: np.ndarray
    overall_percent: float


@dataclass(frozen=True)
class StudyRates:
    """Profit and selected-fuel improvements, carrying their validated basis."""

    profit: MetricData
    carbon: MetricData
    cost: MetricData
    basis: str = "gas"


def validate_basis(basis: str) -> str:
    """Accept only the three explicitly supported system-result scopes."""
    if not isinstance(basis, str) or basis not in BASES:
        raise ValueError(f"basis must be gas, coal or thermal; received {basis!r}")
    return basis


def configure_matplotlib() -> None:
    """Retain the reference fonts, palette-compatible text and transparency."""
    mpl.rcParams.update(
        {
            "font.family": ["Times New Roman", "Arial", "serif"],
            "font.serif": ["Times New Roman", "Times", "DejaVu Serif"],
            "font.size": 11,
            "axes.unicode_minus": True,
            "figure.facecolor": "none",
            "axes.facecolor": "none",
            "savefig.facecolor": "none",
            "savefig.edgecolor": "none",
            "savefig.transparent": True,
        }
    )


def _number(value: object, label: str) -> float:
    if isinstance(value, (bool, np.bool_)):
        raise ValueError(f"{label}: boolean values are not numbers")
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"{label}: expected a finite number") from exc
    if not np.isfinite(result):
        raise ValueError(f"{label}: expected a finite number")
    return result


def _read_summary(
    path: Path, month: str, basis: str = "gas"
) -> dict[str, dict[str, float]]:
    if not path.is_file():
        raise FileNotFoundError(f"missing required system result: {path}")
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        if "Summary" not in workbook.sheetnames:
            raise ValueError(f"{path}: missing Summary worksheet")
        rows = workbook["Summary"].iter_rows(values_only=True)
        header = next(rows, ())
        if len(set(header)) != len(header):
            raise ValueError(f"{path}: duplicate Summary headers")
        missing = set(("Metric", *SCENARIOS)) - set(header)
        if missing:
            raise ValueError(f"{path}: missing Summary columns {sorted(missing)}")
        columns = {value: index for index, value in enumerate(header)}
        selected: dict[str, dict[str, object]] = {}
        for row in rows:
            metric = row[columns["Metric"]]
            if metric not in (*REQUIRED_METRICS, "Basis"):
                continue
            if metric in selected:
                raise ValueError(f"{path}: duplicate required Metric {metric!r}")
            selected[metric] = {
                scenario: row[columns[scenario]] for scenario in SCENARIOS
            }
        absent = set(REQUIRED_METRICS) - set(selected)
        if absent:
            raise ValueError(f"{path}: missing Summary metrics {sorted(absent)}")
    finally:
        workbook.close()

    if "Basis" not in selected:
        if basis != "gas":
            raise ValueError(f"{path}: missing required Basis for {basis}")
    else:
        for scenario in SCENARIOS:
            if selected["Basis"][scenario] != basis:
                raise ValueError(
                    f"{path}: Basis for {scenario} must equal {basis!r}; "
                    f"received {selected['Basis'][scenario]!r}"
                )
    expected_month = f"{month[:4]}-{month[4:]}"
    for scenario in SCENARIOS:
        if selected["Month"][scenario] != expected_month:
            raise ValueError(f"{path}: Summary Month does not match {expected_month}")
    values = {
        metric: {
            scenario: _number(value, f"{path}, {metric}, {scenario}")
            for scenario, value in entries.items()
        }
        for metric, entries in selected.items()
        if metric not in ("Month", "Basis")
    }
    for scenario in SCENARIOS:
        for metric, expected in (
            ("Real_Hours", MONTH_HOURS[month]),
            ("Control_Share", 1),
            ("Selected_ESR_Count", 13),
        ):
            if values[metric][scenario] != expected:
                raise ValueError(f"{path}: {metric}, {scenario} must equal {expected}")
        if not np.isclose(
            values["total_profit"][scenario],
            values["Trading_Profit"][scenario] + values["Final_SOC_Value"][scenario],
            rtol=NUMERIC_RTOL,
            atol=NUMERIC_ATOL,
        ):
            raise ValueError(
                f"{path}: total_profit, {scenario} must preserve trading plus terminal value"
            )
    for metric in ("total_profit", "total_carbon", "total_cost"):
        if values[metric]["Actual"] <= 0:
            raise ValueError(f"{path}: {metric} Actual denominator must be positive")
    return values


def load_study_rates(results_dir: Path, basis: str = "gas") -> StudyRates:
    """Validate all six System Summary tables and derive the original rate formulas.

    Input files are Rolling_Result_202601.xlsx through Rolling_Result_202606.xlsx.
    Every table must describe the same 13-ESR, control_share=1 complete local
    month. All baseline denominators must be positive; missing, duplicate or
    non-finite metrics and altered income valuation stop the plot before export.
    Basis must match in every month. Only legacy gas may omit that metadata.
    """
    basis = validate_basis(basis)
    results_dir = Path(results_dir)
    summaries = [
        _read_summary(results_dir / f"Rolling_Result_{month}.xlsx", month, basis)
        for month in MONTHS
    ]

    def metric_data(metric: str, increase: bool) -> MetricData:
        actual = np.array([values[metric]["Actual"] for values in summaries])
        rolling = np.array([values[metric]["Rolling"] for values in summaries])
        sign = 1.0 if increase else -1.0
        monthly = sign * (rolling / actual - 1.0) * 100.0
        overall = sign * (float(rolling.sum()) / float(actual.sum()) - 1.0) * 100.0
        if not np.isfinite(monthly).all() or not np.isfinite(overall):
            raise ValueError(f"{metric}: derived improvement rates must be finite")
        return MetricData(MONTHS, monthly, overall)

    return StudyRates(
        profit=metric_data("total_profit", True),
        carbon=metric_data("total_carbon", False),
        cost=metric_data("total_cost", False),
        basis=basis,
    )


def _draw_curved_label(
    ax,
    text: str,
    center_degrees: float,
    *,
    span_degrees: float = 58.0,
    radius: float = (ANNULUS_INNER_RADIUS + ANNULUS_OUTER_RADIUS) / 2.0,
) -> None:
    """Spread glyph centers across the requested arc, reversing below."""
    font_size = 12.5
    font = FontProperties(
        family=["Times New Roman", "Arial", "serif"],
        size=font_size,
        weight="bold",
    )
    advances = []
    for character in text:
        if character.isspace():
            width = font_size * 0.34
        else:
            bounds = TextPath((0, 0), character, prop=font).get_extents()
            width = max(float(bounds.width), font_size * 0.22)
        advances.append(width + font_size * 0.045)
    advances = np.asarray(advances)
    positions = (np.cumsum(advances) - advances / 2.0) / advances.sum() - 0.5
    lower_half = 90.0 < center_degrees < 270.0
    direction = -1.0 if lower_half else 1.0
    # Spread the larger glyphs across their sector while retaining edge clearance.
    character_angles = center_degrees + direction * positions * span_degrees
    for character, angle in zip(text, character_angles):
        if character.isspace():
            continue
        rotation = -float(angle) + (180.0 if lower_half else 0.0)
        ax.text(
            np.deg2rad(angle),
            radius,
            character,
            ha="center",
            va="center",
            rotation=rotation,
            rotation_mode="anchor",
            fontproperties=font,
            color=TEXT_COLOR,
            zorder=9,
        )


def draw_radial_panel(
    ax: mpl.axes.Axes,
    data: MetricData,
    title: str,
    color: str,
    annulus_color: str,
    axis_max: float,
    tick_interval: float,
    *,
    bar_width_fraction: float = 0.78,
    label_span_degrees: float = 58.0,
    annulus_width_fraction: float = 1.0,
    annulus_gap: float = ANNULUS_INNER_RADIUS - ZERO_RADIUS,
    annulus_thickness: float = ANNULUS_OUTER_RADIUS - ANNULUS_INNER_RADIUS,
    title_fontsize: float = 15.0,
) -> None:
    """Draw six equal 60-degree sectors using inward linear percent bars.

    Percentage scales are independent of physical radii, keeping the layout
    stable for a 400% profit scale and a 1.2% reduction scale. Values outside
    the positive axis range raise an error instead of being clipped or hidden.
    """
    axis_max = _number(axis_max, "axis_max")
    tick_interval = _number(tick_interval, "tick_interval")
    bar_width_fraction = _number(bar_width_fraction, "bar_width_fraction")
    label_span_degrees = _number(label_span_degrees, "label_span_degrees")
    annulus_width_fraction = _number(annulus_width_fraction, "annulus_width_fraction")
    annulus_gap = _number(annulus_gap, "annulus_gap")
    annulus_thickness = _number(annulus_thickness, "annulus_thickness")
    title_fontsize = _number(title_fontsize, "title_fontsize")
    if axis_max <= 0 or tick_interval <= 0:
        raise ValueError("axis_max and tick_interval must be positive")
    if not 0 < bar_width_fraction <= 1:
        raise ValueError("bar_width_fraction must be greater than 0 and at most 1")
    if not 0 < label_span_degrees <= 360.0 / len(MONTHS):
        raise ValueError("label_span_degrees must be greater than 0 and at most 60")
    if not 0 < annulus_width_fraction <= 1:
        raise ValueError("annulus_width_fraction must be greater than 0 and at most 1")
    if annulus_gap < 0:
        raise ValueError("annulus_gap must be non-negative")
    if annulus_thickness <= 0:
        raise ValueError("annulus_thickness must be positive")
    if title_fontsize <= 0:
        raise ValueError("title_fontsize must be positive")
    tick_count = axis_max / tick_interval
    if not np.isclose(tick_count, round(tick_count)):
        raise ValueError("tick_interval must divide axis_max")
    if data.months != MONTHS:
        raise ValueError("radial panel must contain all six ordered 2026 months")
    raw_values = np.asarray(data.monthly_percent, dtype=object)
    if raw_values.shape != (len(MONTHS),):
        raise ValueError("radial panel requires exactly six monthly percentages")
    values = np.array([_number(value, "monthly percentage") for value in raw_values])
    overall = _number(data.overall_percent, "Overall percentage")
    if (values < 0).any() or (values > axis_max).any():
        raise ValueError(f"monthly percentages must fit the 0–{axis_max:g}% scale")

    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)
    annulus_inner = ZERO_RADIUS + annulus_gap
    annulus_outer = annulus_inner + annulus_thickness
    ax.set_ylim(0, annulus_outer + PLOT_LIMIT - ANNULUS_OUTER_RADIUS)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.grid(False)
    ax.spines["polar"].set_visible(False)
    ax.set_facecolor("none")

    span = ZERO_RADIUS - INNER_RADIUS
    circle = np.linspace(0, 2 * np.pi, 721)
    ticks = np.arange(round(tick_count) + 1, dtype=float) * tick_interval
    for tick in ticks:
        radius = ZERO_RADIUS - tick / axis_max * span
        ax.plot(
            circle,
            np.full_like(circle, radius),
            color=GRID_COLOR,
            linewidth=2.0 if tick == 0 else 1.8,
            linestyle="-" if tick == 0 else (0, (3.2, 2.4)),
            alpha=0.78,
            zorder=0,
        )
        ax.text(
            np.deg2rad(135),
            radius,
            f"{tick:g}%",
            ha="center",
            va="center",
            fontsize=11.5,
            fontweight="semibold",
            color=TEXT_COLOR,
            zorder=8,
        )

    sector = 2 * np.pi / len(MONTHS)
    angles = (np.arange(len(MONTHS)) + 0.5) * sector
    heights = values / axis_max * span
    ax.bar(
        angles,
        heights,
        width=sector * bar_width_fraction,
        bottom=ZERO_RADIUS - heights,
        color=color,
        edgecolor="white",
        linewidth=0.35,
        alpha=0.94,
        zorder=3,
    )
    ax.bar(
        angles,
        np.full(len(MONTHS), annulus_thickness),
        width=sector * annulus_width_fraction,
        bottom=annulus_inner,
        color=annulus_color,
        edgecolor="white",
        linewidth=1.35,
        zorder=2,
    )
    for radius in (annulus_inner, annulus_outer):
        ax.plot(circle, np.full_like(circle, radius), color=color, lw=0.7, zorder=4)
    ax.plot(
        circle,
        np.full_like(circle, INNER_RADIUS),
        color=GRID_COLOR,
        linewidth=1.1,
        zorder=5,
    )
    for angle, month, value in zip(angles, MONTHS, values):
        month_label = calendar.month_abbr[int(month[4:])].upper()
        _draw_curved_label(
            ax,
            f"{month_label}: {value:.2f}%",
            float(np.rad2deg(angle)),
            span_degrees=label_span_degrees,
            radius=(annulus_inner + annulus_outer) / 2.0,
        )

    ax.text(
        0.5,
        1.075,
        title,
        transform=ax.transAxes,
        ha="center",
        va="bottom",
        fontsize=title_fontsize,
        fontweight="bold",
        color=TEXT_COLOR,
        clip_on=False,
    )
    ax.text(
        0.5,
        1.015,
        f"(Overall: {overall:.2f}%)",
        transform=ax.transAxes,
        ha="center",
        va="bottom",
        fontsize=14,
        fontweight="semibold",
        color=TEXT_COLOR,
        clip_on=False,
    )

    # Retain the reference's bottom-left dashed scale key; its value follows
    # the actual spacing between neighboring radial grid rings.
    ax.plot(
        [0.055, 0.145],
        [0.03, 0.03],
        transform=ax.transAxes,
        color=GRID_COLOR,
        linewidth=2.0,
        linestyle=(0, (3.2, 2.4)),
        alpha=0.95,
        clip_on=False,
    )
    for endpoint in (0.055, 0.145):
        ax.plot(
            [endpoint, endpoint],
            [0.03, 0.06],
            transform=ax.transAxes,
            color=GRID_COLOR,
            linewidth=2.0,
            alpha=0.95,
            clip_on=False,
        )
    ax.text(
        0.165,
        0.04,
        f"{tick_interval:g}%",
        transform=ax.transAxes,
        ha="left",
        va="center",
        fontsize=12,
        fontweight="bold",
        color=TEXT_COLOR,
        clip_on=False,
    )
