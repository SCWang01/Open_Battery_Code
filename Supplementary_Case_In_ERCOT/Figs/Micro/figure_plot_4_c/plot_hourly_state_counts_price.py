"""Plot mean ESR state counts with the monthly Hub price profile.

Classified counts are summed across all configured ESRs, then divided by their
fixed number. Independent Hub prices provide a daily sample at each clock
hour for a pointwise, approximate 90% Student-t mean confidence interval.
This module preserves Figure C's stacked-count design and exports a 720 dpi
PNG with source-data CSV. The standalone CLI defaults to May 2026.
"""

from __future__ import annotations

import csv
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from numbers import Integral
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import MaxNLocator

MICRO_FIGURES_DIR = Path(__file__).resolve().parents[1]
if str(MICRO_FIGURES_DIR) not in sys.path:
    sys.path.insert(0, str(MICRO_FIGURES_DIR))

from esr_figure_data import (
    COLORS,
    CONFIDENCE_LEVEL,
    STATE_ORDER,
    ESRFigureData,
    HubPriceData,
    HourlyPriceSummary,
    StateCountData,
    configured_resources,
    create_argument_parser,
    load_hub_price_data,
    load_month_data,
    save_png,
)


PRICE_COLOR = "#B13AA3"
CI_FILL_ALPHA = 0.14
CI_HATCH = "//////"
BAR_WIDTH = 0.52
FIGURE_WIDTH_MM = 180.0
FIGURE_HEIGHT_MM = 64.0
AGGREGATE_OUTPUT_DIR = Path(__file__).resolve().parent / "Figs"


@dataclass(frozen=True)
class MeanStateCountData:
    """Per-ESR means retain fractional state counts at every clock hour."""

    counts: dict[str, list[float]]
    valid: list[float]


@dataclass(frozen=True)
class AggregatedFigureData:
    """Auditable pooled counts, fixed-population means, and independent prices."""

    resources: tuple[str, ...]
    month: str
    raw_states: StateCountData
    mean_states: MeanStateCountData
    hub_prices: HubPriceData

    @property
    def raw_rows(self) -> int:
        return sum(self.raw_states.raw)

    @property
    def valid_rows(self) -> int:
        return sum(self.raw_states.valid)

    @property
    def excluded_zero_limit(self) -> int:
        return sum(self.raw_states.excluded_zero_limit)


def _validate_member_counts(data: ESRFigureData) -> None:
    """Reconcile clock-hour observations before pooling or changing units."""
    states = data.hourly_states
    if set(states.counts) != set(STATE_ORDER):
        raise ValueError(f"{data.resource}: hourly counts must contain seven states.")
    series = {
        **states.counts,
        "valid": states.valid,
        "excluded_zero_limit": states.excluded_zero_limit,
        "excluded_nc": states.excluded_nc,
        "raw": states.raw,
    }
    for name, values in series.items():
        if len(values) != 24 or any(
            isinstance(value, bool) or not isinstance(value, Integral) or value < 0
            for value in values
        ):
            raise ValueError(
                f"{data.resource}: {name} must contain 24 nonnegative integer counts."
            )
    for hour in range(24):
        if (
            sum(states.counts[state][hour] for state in STATE_ORDER)
            != states.valid[hour]
        ):
            raise ValueError(f"{data.resource}: valid counts disagree at hour {hour}.")
        if (
            states.valid[hour]
            + states.excluded_zero_limit[hour]
            + states.excluded_nc[hour]
            != states.raw[hour]
        ):
            raise ValueError(f"{data.resource}: raw counts disagree at hour {hour}.")
    if any(
        sum(states.counts[state]) != data.state_counts[state] for state in STATE_ORDER
    ) or any(
        state not in STATE_ORDER and value
        for state, value in data.state_counts.items()
    ):
        raise ValueError(f"{data.resource}: hourly states disagree with monthly counts.")
    if (
        sum(states.raw) != data.raw_rows
        or sum(states.excluded_zero_limit) != data.excluded_zero_limit
    ):
        raise ValueError(f"{data.resource}: hourly audits disagree with monthly totals.")


def _validate_plot_data(
    states: MeanStateCountData, summaries: list[HourlyPriceSummary]
) -> None:
    """Reject omitted hours, invalid means, or incomplete confidence bands."""
    if set(states.counts) != set(STATE_ORDER):
        raise ValueError("Mean counts must contain exactly seven states.")
    for values in (*states.counts.values(), states.valid):
        if len(values) != 24 or any(
            not math.isfinite(value) or value < 0 for value in values
        ):
            raise ValueError("Mean counts must contain 24 finite nonnegative values.")
    if any(
        not math.isclose(
            sum(states.counts[state][hour] for state in STATE_ORDER),
            states.valid[hour],
            rel_tol=1e-12,
            abs_tol=1e-10,
        )
        for hour in range(24)
    ):
        raise ValueError("Mean state counts do not reconcile to mean valid counts.")
    if max(states.valid) <= 0:
        raise ValueError("No valid ESR state counts to plot.")
    if [summary.hour for summary in summaries] != list(range(24)):
        raise ValueError("Hub price summaries must cover hours 0 through 23 in order.")
    for summary in summaries:
        if (
            not isinstance(summary.sample_size, Integral)
            or summary.sample_size < 2
            or not all(
                math.isfinite(value)
                for value in (
                    summary.mean,
                    summary.ci_low,
                    summary.ci_high,
                    summary.standard_deviation,
                )
            )
            or summary.standard_deviation < 0
            or not summary.ci_low <= summary.mean <= summary.ci_high
        ):
            raise ValueError(f"Hour {summary.hour}: invalid Hub price mean or interval.")


def aggregate_figure_data(
    members: Sequence[ESRFigureData], hub: HubPriceData
) -> AggregatedFigureData:
    """Average complete resource counts; Hub sample sizes remain independent."""
    if not members:
        raise ValueError("No ESR data supplied for Figure 4c.")
    resources = configured_resources()
    supplied = [data.resource for data in members]
    if len(set(supplied)) != len(supplied) or set(supplied) != set(resources):
        raise ValueError("Figure 4c requires each configured ESR exactly once.")
    month = members[0].month
    if hub.month != month or any(data.month != month for data in members):
        raise ValueError("ESR counts and Hub prices must share one calendar month.")
    pooled = StateCountData(
        {state: [0] * 24 for state in STATE_ORDER},
        [0] * 24,
        [0] * 24,
        [0] * 24,
        [0] * 24,
    )
    for data in members:
        _validate_member_counts(data)
        states = data.hourly_states
        for state in STATE_ORDER:
            for hour in range(24):
                pooled.counts[state][hour] += states.counts[state][hour]
        for name in ("valid", "excluded_zero_limit", "excluded_nc", "raw"):
            values = getattr(pooled, name)
            for hour, value in enumerate(getattr(states, name)):
                values[hour] += value
    resource_count = len(resources)
    means = MeanStateCountData(
        {
            state: [value / resource_count for value in pooled.counts[state]]
            for state in STATE_ORDER
        },
        [value / resource_count for value in pooled.valid],
    )
    _validate_plot_data(means, hub.prices)
    return AggregatedFigureData(resources, month, pooled, means, hub)


def configure_matplotlib() -> None:
    """Configure publication typography and editable vector text."""
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
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
            "hatch.linewidth": 0.55,
            "savefig.transparent": True,
        }
    )


def nice_count_scale(maximum: float) -> tuple[int, list[int]]:
    """Return a data-driven count-axis ceiling with about 10% headroom."""
    if not math.isfinite(maximum) or maximum <= 0:
        raise ValueError(f"Count-axis maximum must be positive; found {maximum}.")
    raw_step = maximum / 7.0
    magnitude = 1.0
    if raw_step >= 1.0:
        while magnitude * 10.0 <= raw_step:
            magnitude *= 10.0
    else:
        while magnitude > raw_step:
            magnitude /= 10.0
    step = min(
        (factor * magnitude for factor in (1, 2, 2.5, 5, 10)),
        key=lambda candidate: abs(candidate - raw_step),
    )
    integer_step = max(1, int(math.ceil(step)))
    upper = int(math.ceil(maximum / integer_step) * integer_step)
    if upper < maximum * 1.08:
        upper += integer_step
    return upper, list(range(0, upper + 1, integer_step))


def price_axis_limits(summaries: list[HourlyPriceSummary]) -> tuple[float, float]:
    """Cover the complete CI extent with 10% data-relative headroom."""
    minimum = min(summary.ci_low for summary in summaries)
    maximum = max(summary.ci_high for summary in summaries)
    span = maximum - minimum
    if math.isclose(span, 0.0, rel_tol=0.0, abs_tol=1e-12):
        span = max(abs(minimum), 1.0)
    padding = 0.10 * span
    return minimum - padding, maximum + padding


def build_figure(
    state_data: MeanStateCountData,
    summaries: list[HourlyPriceSummary],
) -> plt.Figure:
    """Create the transparent stacked-bar and price-confidence-band figure."""
    _validate_plot_data(state_data, summaries)
    configure_matplotlib()
    mm_per_inch = 25.4
    figure, count_axis = plt.subplots(
        figsize=(FIGURE_WIDTH_MM / mm_per_inch, FIGURE_HEIGHT_MM / mm_per_inch),
        facecolor="none",
    )
    figure.subplots_adjust(left=0.075, right=0.905, bottom=0.22, top=0.82)
    count_axis.set_facecolor("none")
    price_axis = count_axis.twinx()
    price_axis.set_facecolor("none")

    hours = list(range(24))
    bottoms = [0] * 24
    for state in STATE_ORDER:
        values = state_data.counts[state]
        count_axis.bar(
            hours,
            values,
            width=BAR_WIDTH,
            bottom=bottoms,
            color=COLORS[state],
            edgecolor="none",
            linewidth=0,
            zorder=2,
        )
        bottoms = [bottom + value for bottom, value in zip(bottoms, values)]

    means = [summary.mean for summary in summaries]
    lower = [summary.ci_low for summary in summaries]
    upper = [summary.ci_high for summary in summaries]
    price_axis.fill_between(
        hours,
        lower,
        upper,
        facecolor=mpl.colors.to_rgba(PRICE_COLOR, CI_FILL_ALPHA),
        edgecolor=PRICE_COLOR,
        hatch=CI_HATCH,
        linewidth=0.0,
        zorder=3,
    )
    price_axis.plot(
        hours,
        means,
        color=PRICE_COLOR,
        linewidth=1.15,
        marker="D",
        markersize=3.0,
        markerfacecolor="white",
        markeredgecolor=PRICE_COLOR,
        markeredgewidth=0.8,
        zorder=4,
    )

    count_upper, count_ticks = nice_count_scale(max(state_data.valid))
    count_axis.set_xlim(-0.7, 23.7)
    count_axis.set_ylim(0, count_upper)
    count_axis.set_xticks(hours)
    count_axis.set_yticks(count_ticks)
    count_axis.set_xlabel("Time (hours)")
    count_axis.set_ylabel("Mean count per ESR")
    count_axis.tick_params(axis="both", direction="out", length=2.8, width=0.65)

    price_axis.set_ylim(*price_axis_limits(summaries))
    price_axis.yaxis.set_major_locator(MaxNLocator(nbins=6))
    price_axis.set_ylabel("Hub price (USD/MWh)", color=PRICE_COLOR)
    price_axis.tick_params(
        axis="y",
        colors=PRICE_COLOR,
        direction="out",
        length=2.8,
        width=0.65,
    )

    frame_color = "#B7B7B7"
    for axis in (count_axis, price_axis):
        for spine in axis.spines.values():
            spine.set_color(frame_color)
            spine.set_linewidth(0.65)
    count_axis.spines["right"].set_visible(False)
    price_axis.spines["left"].set_visible(False)

    figure.legend(
        handles=[
            Patch(
                facecolor=mpl.colors.to_rgba(PRICE_COLOR, CI_FILL_ALPHA),
                edgecolor=PRICE_COLOR,
                hatch=CI_HATCH,
                linewidth=0,
                label="90% Confidence interval",
            ),
            Line2D(
                [0],
                [0],
                color=PRICE_COLOR,
                linewidth=1.15,
                marker="D",
                markersize=3.0,
                markerfacecolor="white",
                markeredgecolor=PRICE_COLOR,
                markeredgewidth=0.8,
                label="Average price",
            ),
        ],
        loc="upper center",
        bbox_to_anchor=(0.5, 0.985),
        ncol=2,
        frameon=False,
        handlelength=1.6,
        handletextpad=0.35,
        columnspacing=5.5,
        borderaxespad=0,
    )
    return figure


def source_data_rows(data: AggregatedFigureData) -> list[dict[str, object]]:
    """Keep raw count totals, fractional means, and actual daily price n."""
    _validate_plot_data(data.mean_states, data.hub_prices.prices)
    rows = []
    for hour, price in enumerate(data.hub_prices.prices):
        row = {
            "month": data.month,
            "resource_count": len(data.resources),
            "resources": ";".join(data.resources),
            "hour": hour,
        }
        for state in STATE_ORDER:
            row[f"{state}_total"] = data.raw_states.counts[state][hour]
            row[f"{state}_mean"] = data.mean_states.counts[state][hour]
        row.update(
            {
                "raw_total": data.raw_states.raw[hour],
                "valid_total": data.raw_states.valid[hour],
                "excluded_zero_limit_total": data.raw_states.excluded_zero_limit[hour],
                "excluded_nc_total": data.raw_states.excluded_nc[hour],
                "mean_valid": data.mean_states.valid[hour],
                "hub_price_mean": price.mean,
                "hub_price_sd": price.standard_deviation,
                "hub_price_n": price.sample_size,
                "hub_price_ci_low": price.ci_low,
                "hub_price_ci_high": price.ci_high,
                "confidence_level": CONFIDENCE_LEVEL,
                "ci_method": "Pointwise Student-t; approximately independent days",
            }
        )
        rows.append(row)
    return rows


def plot_aggregate(
    data: AggregatedFigureData, output_dir: Path = AGGREGATE_OUTPUT_DIR
) -> Path:
    """Export one mean-ESR Figure C with Hub confidence bands and source CSV."""
    rows = source_data_rows(data)
    figure = build_figure(data.mean_states, data.hub_prices.prices)
    path = output_dir / f"Fig_4_c_ALL_ESR_{data.month}.png"
    try:
        save_png(
            figure,
            path,
            f"Figure 4c: all {len(data.resources)} ESR mean counts "
            f"and Hub prices, {data.month}",
        )
    finally:
        plt.close(figure)
    csv_path = path.with_name(f"{path.stem}_source_data.csv")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


def main() -> None:
    """Validate every ESR and the selected Hub month before exporting."""
    parser = create_argument_parser(__doc__, allow_resource=False)
    args = parser.parse_args()
    try:
        members = [
            load_month_data(resource, args.month)
            for resource in configured_resources()
        ]
        data = aggregate_figure_data(members, load_hub_price_data(args.month))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    path = plot_aggregate(data)
    print(
        f"ALL_ESR {args.month}: mean over {len(data.resources)} resources; "
        f"{data.valid_rows}/{data.raw_rows} pooled state hours; "
        f"zero-limit exclusions={data.excluded_zero_limit}; "
        f"Hub daily samples/hour={','.join(str(price.sample_size) for price in data.hub_prices.prices)}"
    )
    print(path)
    print(path.with_name(f"{path.stem}_source_data.csv"))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Error: {exc}") from None
