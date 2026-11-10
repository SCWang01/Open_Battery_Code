"""Plot all configured ESRs' pooled monthly state distributions.

Panel (a) normalizes the five classified price-width totals, and panel (b)
normalizes the seven hourly P_ESS_state counts. Both pool raw values across
resources before normalization, rather than averaging resource percentages.
The shared monthly loader
owns source validation and exclusions. This module draws only the requested
transparent, 180 x 70 mm PNG; its default selection is May 2026.
"""

from __future__ import annotations

import csv
import math
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from numbers import Integral
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D


MICRO_FIGS_DIR = Path(__file__).resolve().parents[1]
if str(MICRO_FIGS_DIR) not in sys.path:
    sys.path.insert(0, str(MICRO_FIGS_DIR))

from esr_figure_data import (  # noqa: E402
    COLORS,
    LEGEND_LABELS,
    STATE_ORDER,
    WIDTH_STATE_ORDER,
    ESRFigureData,
    configured_resources,
    create_argument_parser,
    load_month_data,
    save_png,
)


FIGURE_WIDTH_MM = 180.0
AGGREGATE_OUTPUT_DIR = Path(__file__).resolve().parent / "Figs"
FIGURE_HEIGHT_MM = 70.0
PANEL_WIDTH_RATIOS = (1.10, 1.03, 1.30)
RIGHT_PIE_RADIUS = 0.82
INSIDE_MIN_PERCENT = 5.0
LABEL_MIN_GAP = 0.23
LABEL_Y_LIMIT = 0.93


@dataclass(frozen=True)
class AggregatedDistributions:
    """Raw monthly totals for the complete configured resource population."""

    resources: tuple[str, ...]
    month: str
    width_sums: dict[str, float]
    state_counts: Counter[str]
    raw_rows: int
    excluded_zero_limit: int

    @property
    def valid_rows(self) -> int:
        return sum(self.state_counts.values())


def configure_matplotlib() -> None:
    """Preserve the supplied figure's typography, colors and transparency."""
    mpl.rcParams.update(
        {
            "font.family": ["Times New Roman", "Arial", "DejaVu Serif"],
            "font.serif": ["Times New Roman", "Times", "DejaVu Serif"],
            "font.size": 9,
            "legend.frameon": False,
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
            "axes.unicode_minus": False,
        }
    )


def _validate_distributions(
    state_counts: Counter[str], width_sums: dict[str, float], record_count: int,
    *, allow_empty: bool = False,
) -> None:
    """Reject invalid denominators or omitted categories before drawing."""
    if not isinstance(record_count, Integral) or record_count < 0:
        raise ValueError("record_count must be a nonnegative integer.")
    if not allow_empty and record_count == 0:
        raise ValueError("record_count must be positive for a plotted distribution.")
    for state, count in state_counts.items():
        if not isinstance(count, Integral) or count < 0:
            raise ValueError(f"State {state!r} has invalid count {count!r}.")
        if state not in STATE_ORDER and count:
            raise ValueError(f"Unplotted state {state!r} has {count} records.")
    if sum(state_counts.values()) != record_count:
        raise ValueError("State counts do not reconcile to record_count.")
    if set(width_sums) != set(WIDTH_STATE_ORDER):
        raise ValueError("width_sums must contain exactly CC/CD/NU/DC/DD.")
    for state, width in width_sums.items():
        if not math.isfinite(width) or width < 0:
            raise ValueError(f"State {state!r} has invalid width {width!r}.")
    if record_count == 0 and sum(width_sums.values()) > 0:
        raise ValueError("A member without valid states cannot contribute price widths.")
    if not allow_empty and sum(width_sums.values()) <= 0:
        raise ValueError("The five dsfunction price-width totals are all zero.")


def percent_text(percent: float) -> str:
    return f"{percent:.2f}%"


def _spaced_label_positions(desired: list[float]) -> list[float]:
    """Place sorted external labels without collapsing tiny pie segments."""
    positions: list[float] = []
    for target in desired:
        lower = positions[-1] + LABEL_MIN_GAP if positions else -LABEL_Y_LIMIT
        positions.append(max(target, lower))
    if positions and positions[-1] > LABEL_Y_LIMIT:
        positions[-1] = LABEL_Y_LIMIT
        for index in range(len(positions) - 2, -1, -1):
            positions[index] = min(
                positions[index], positions[index + 1] - LABEL_MIN_GAP
            )
    return positions


def _draw_external_labels(
    ax: plt.Axes,
    labels: list[tuple[float, float]],
    radius: float,
) -> None:
    """Draw percentages and leaders on the actual side of their pie slice."""
    for side in (-1, 1):
        selected = [
            (angle, percent)
            for angle, percent in labels
            if (1 if math.cos(angle) >= 0 else -1) == side
        ]
        selected.sort(key=lambda item: math.sin(item[0]))
        desired = [
            max(-LABEL_Y_LIMIT, min(LABEL_Y_LIMIT, 1.01 * math.sin(angle)))
            for angle, _ in selected
        ]
        positions = _spaced_label_positions(desired)
        for (angle, percent), text_y in zip(selected, positions):
            anchor_x = radius * 0.98 * math.cos(angle)
            anchor_y = radius * 0.98 * math.sin(angle)
            elbow_x = radius * 1.09 * math.cos(angle)
            elbow_y = radius * 1.09 * math.sin(angle)
            text_x = side * 1.00
            ax.plot(
                [anchor_x, elbow_x, text_x - side * 0.035],
                [anchor_y, elbow_y, text_y],
                color="#B7B7B7",
                linewidth=0.65,
                solid_capstyle="round",
                clip_on=False,
                zorder=1,
            )
            ax.text(
                text_x,
                text_y,
                percent_text(percent),
                ha="left" if side > 0 else "right",
                va="center",
                fontsize=9,
                color="#222222",
                clip_on=False,
                zorder=2,
            )


def _draw_pie(
    ax: plt.Axes,
    states: tuple[str, ...],
    values_by_state: dict[str, float] | Counter[str],
    inside_states: set[str],
    radius: float,
) -> None:
    plotted = [state for state in states if values_by_state[state] > 0]
    values = [values_by_state[state] for state in plotted]
    total = sum(values)
    wedges, _ = ax.pie(
        values,
        colors=[COLORS[state] for state in plotted],
        startangle=90,
        counterclock=False,
        radius=radius,
        wedgeprops={"linewidth": 0, "edgecolor": "none"},
        normalize=True,
    )
    outside: list[tuple[float, float]] = []
    inside: list[tuple[float, float, mpl.text.Text]] = []
    for state, value, wedge in zip(plotted, values, wedges):
        percent = 100.0 * value / total
        angle = math.radians((wedge.theta1 + wedge.theta2) / 2.0)
        if state in inside_states and percent >= INSIDE_MIN_PERCENT:
            label_radius = radius * (0.59 if state == "NU" else 0.65)
            text = ax.text(
                label_radius * math.cos(angle),
                label_radius * math.sin(angle),
                percent_text(percent),
                ha="center",
                va="center",
                fontsize=9,
                color="#FFFFFF" if state in {"MC", "MD"} else "#222222",
            )
            inside.append((angle, percent, text))
        else:
            outside.append((angle, percent))
    ax.set_xlim(-1.40, 1.40)
    ax.set_ylim(-1.07, 1.07)
    ax.set_aspect("equal")
    ax.set_axis_off()
    # Slice angles alone cannot predict label collisions near the top or
    # bottom. Check actual font bounds and send the smaller share outside.
    while len(inside) > 1:
        ax.figure.canvas.draw()
        renderer = ax.figure.canvas.get_renderer()
        boxes = [
            text.get_window_extent(renderer).expanded(1.04, 1.08)
            for _, _, text in inside
        ]
        collision = next(
            (
                (i, j)
                for i, first in enumerate(boxes)
                for j, second in enumerate(boxes)
                if j > i and first.overlaps(second)
            ),
            None,
        )
        if collision is None:
            break
        remove_index = min(collision, key=lambda index: inside[index][1])
        angle, percent, text = inside.pop(remove_index)
        text.remove()
        outside.append((angle, percent))
    _draw_external_labels(ax, outside, radius)


def draw_left_pie(ax: plt.Axes, width_sums: dict[str, float]) -> None:
    # Compensate for subplot widths so both circles have the same displayed diameter.
    radius = RIGHT_PIE_RADIUS * PANEL_WIDTH_RATIOS[2] / PANEL_WIDTH_RATIOS[0]
    _draw_pie(ax, WIDTH_STATE_ORDER, width_sums, set(WIDTH_STATE_ORDER), radius)


def draw_right_pie(ax: plt.Axes, state_counts: Counter[str], record_count: int) -> None:
    _draw_pie(ax, STATE_ORDER, state_counts, {"MC", "NU", "MD"}, RIGHT_PIE_RADIUS)


def draw_legend(ax: plt.Axes) -> None:
    handles = [
        Line2D(
            [0],
            [0],
            marker="s",
            linestyle="None",
            markersize=7.2,
            markerfacecolor=COLORS[state],
            markeredgecolor="none",
        )
        for state in STATE_ORDER
    ]
    ax.legend(
        handles,
        [LEGEND_LABELS[state] for state in STATE_ORDER],
        loc="center",
        fontsize=9,
        handlelength=0.8,
        handletextpad=0.45,
        labelspacing=0.53,
        borderaxespad=0,
        frameon=False,
    )
    ax.set_axis_off()


def build_figure(
    state_counts: Counter[str], width_sums: dict[str, float], record_count: int
) -> plt.Figure:
    """Return the validated a+b figure; the caller owns saving and closing."""
    _validate_distributions(state_counts, width_sums, record_count)
    configure_matplotlib()
    fig = plt.figure(
        figsize=(FIGURE_WIDTH_MM / 25.4, FIGURE_HEIGHT_MM / 25.4),
        facecolor="none",
    )
    grid = fig.add_gridspec(
        1,
        3,
        width_ratios=PANEL_WIDTH_RATIOS,
        left=0.018,
        right=0.988,
        bottom=0.135,
        top=0.985,
        wspace=0.015,
    )
    left_ax = fig.add_subplot(grid[0, 0])
    legend_ax = fig.add_subplot(grid[0, 1])
    right_ax = fig.add_subplot(grid[0, 2])
    draw_left_pie(left_ax, width_sums)
    draw_legend(legend_ax)
    draw_right_pie(right_ax, state_counts, record_count)
    return fig


def aggregate_distributions(
    members: Sequence[ESRFigureData],
) -> AggregatedDistributions:
    """Sum all members' raw values; every state observation has equal weight."""
    if not members:
        raise ValueError("No ESR data supplied for aggregation.")
    resources = configured_resources()
    supplied = [data.resource for data in members]
    if len(set(supplied)) != len(supplied):
        raise ValueError("Duplicate ESR data supplied for aggregation.")
    if set(supplied) != set(resources):
        missing = sorted(set(resources) - set(supplied))
        unexpected = sorted(set(supplied) - set(resources))
        raise ValueError(
            f"Aggregation requires all configured ESRs; "
            f"missing={missing}, unexpected={unexpected}."
        )
    month = members[0].month
    counts = Counter(dict.fromkeys(STATE_ORDER, 0))
    widths = dict.fromkeys(WIDTH_STATE_ORDER, 0.0)
    raw_rows = 0
    excluded = 0
    for data in members:
        if data.month != month:
            raise ValueError("Aggregation requires one common calendar month.")
        _validate_distributions(
            data.state_counts, data.width_sums, data.valid_rows, allow_empty=True
        )
        for name, value in (
            ("raw_rows", data.raw_rows),
            ("excluded_zero_limit", data.excluded_zero_limit),
        ):
            if not isinstance(value, Integral) or value < 0:
                raise ValueError(f"{data.resource}: {name} must be a nonnegative integer.")
        if data.valid_rows + data.excluded_zero_limit != data.raw_rows:
            raise ValueError(f"{data.resource}: state counts do not reconcile to raw rows.")
        counts.update(data.state_counts)
        for state in WIDTH_STATE_ORDER:
            widths[state] += data.width_sums[state]
        raw_rows += data.raw_rows
        excluded += data.excluded_zero_limit
    _validate_distributions(counts, widths, raw_rows - excluded)
    return AggregatedDistributions(resources, month, widths, counts, raw_rows, excluded)


def source_data_rows(data: AggregatedDistributions) -> list[dict[str, object]]:
    """Expose unrounded totals and percentages with both denominator audits."""
    _validate_distributions(data.state_counts, data.width_sums, data.valid_rows)
    rows = []
    for panel, states, values in (
        ("a", WIDTH_STATE_ORDER, data.width_sums),
        ("b", STATE_ORDER, data.state_counts),
    ):
        total = sum(values[state] for state in states)
        for state in states:
            value = values[state]
            rows.append(
                {
                    "month": data.month,
                    "resource_count": len(data.resources),
                    "resources": ";".join(data.resources),
                    "panel": panel,
                    "state": state,
                    "value": value,
                    "total": total,
                    "percentage": 100.0 * value / total,
                    "raw_rows": data.raw_rows,
                    "valid_rows": data.valid_rows,
                    "excluded_zero_limit": data.excluded_zero_limit,
                }
            )
    return rows


def plot_aggregate(
    data: AggregatedDistributions, output_dir: Path = AGGREGATE_OUTPUT_DIR
) -> Path:
    """Save one pooled 720 dpi PNG and its unrounded twelve-category CSV."""
    rows = source_data_rows(data)
    fig = build_figure(data.state_counts, data.width_sums, data.valid_rows)
    path = output_dir / f"Fig_4_a_b_ALL_ESR_{data.month}.png"
    try:
        save_png(
            fig,
            path,
            f"All {len(data.resources)} ESRs {data.month}: pooled state distributions",
        )
    finally:
        plt.close(fig)
    csv_path = path.with_name(f"{path.stem}_source_data.csv")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


def main() -> None:
    parser = create_argument_parser(__doc__, allow_resource=False)
    args = parser.parse_args()
    try:
        data = aggregate_distributions(
            [load_month_data(resource, args.month) for resource in configured_resources()]
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    path = plot_aggregate(data)
    print(
        f"ALL_ESR {args.month}: {len(data.resources)} resources; "
        f"{data.valid_rows}/{data.raw_rows} state hours; "
        f"excluded zero-limit hours={data.excluded_zero_limit}"
    )
    print(path)
    print(path.with_name(f"{path.stem}_source_data.csv"))


if __name__ == "__main__":
    main()
