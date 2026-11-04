"""Plot six-month telemetry and M2 totals from the Comparison worksheet.

Run ``python -B Profit_Increment.py`` to read Results/Micro_Results/Comparison_Month.xlsx
and export resource figures, a combined-total figure, and a three-column
overview to Figs/Micro/Six_Month_Comparison_1hour. With 13 resources and ALL_RESOURCES,
the overview is 5 by 3 with one unused panel hidden (15 figures, 45 files).
Sum_Realized and Sum_Rolling include six-month trading profit plus the sum of
monthly terminal SOC values. Overall growth uses the combined dollar totals,
not the mean of resource growth rates. Growth is undefined for a nonpositive
telemetry total. This script only reads the summary and never recomputes profit.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path
import sys

# Direct file execution must locate the shared Micro modules regardless of cwd.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Program' / 'Micro'))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import font_manager
from matplotlib.ticker import MaxNLocator, StrMethodFormatter

from classify_p_ess import validate_resource_name


from project_paths import PROJECT_ROOT as ROOT, MICRO_RESULTS_DIR, MICRO_FIGS_DIR
REQUIRED_COLUMNS = ("Resource", "Sum_Realized", "Sum_Rolling")
GRID_COLUMNS = 3
TOTAL_RESOURCE = "ALL_RESOURCES"
TOTAL_FIGURE_NAME = "Profit_Increment_Total"
CATEGORIES = ("Telemetry", "M2")
COLORS = ("#A6A6A6", "#3274A1")
PNG_DPI = 600
OVERVIEW_NAME = "Profit_Increment_All_Resources"


@dataclass(frozen=True)
class ResourceProfit:
    """Six-month dollar totals for one resource or the combined resource total."""

    resource: str
    telemetry: float
    m2: float

    @property
    def growth_rate(self) -> float | None:
        """Return fractional growth; a nonpositive baseline has no growth rate."""
        if self.telemetry <= 0:
            return None
        return (self.m2 - self.telemetry) / self.telemetry


def _ordered_records(records: list[ResourceProfit]) -> list[ResourceProfit]:
    """Keep individual resources alphabetic and the combined total last."""
    return sorted(records, key=lambda record: (record.resource == TOTAL_RESOURCE, record.resource))


def validate_comparison(frame: pd.DataFrame, source: Path) -> list[ResourceProfit]:
    """Validate unique resources and one total row, without fixing resource count."""
    missing = set(REQUIRED_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"{source}: missing required columns {sorted(missing)}")
    if len(frame) < 2:
        raise ValueError(
            f"{source}: expected at least one resource and an {TOTAL_RESOURCE} total row"
        )
    records = []
    seen = set()
    for excel_row, values in enumerate(
        frame.loc[:, REQUIRED_COLUMNS].itertuples(index=False, name=None), start=2
    ):
        resource = validate_resource_name(values[0], input_path=source)
        key = resource.casefold()
        if key in seen:
            raise ValueError(
                f"{source}: row {excel_row}: duplicate resource {resource!r}"
            )
        if key == TOTAL_RESOURCE.casefold():
            resource = TOTAL_RESOURCE
        elif f"Profit_Increment_{resource}".casefold() == TOTAL_FIGURE_NAME.casefold():
            raise ValueError(f"{source}: resource name collides with the total figure: {resource!r}")
        seen.add(key)
        numbers = []
        for column, value in zip(REQUIRED_COLUMNS[1:], values[1:]):
            try:
                if isinstance(value, (bool, np.bool_)):
                    raise ValueError("boolean profit")
                number = float(value)
                if not math.isfinite(number):
                    raise ValueError("nonfinite profit")
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(
                    f"{source}: row {excel_row}, resource {resource!r}, "
                    f"column {column!r}: expected a finite number, got {value!r}"
                ) from exc
            numbers.append(number)
        record = ResourceProfit(resource, *numbers)
        if record.growth_rate is not None and not math.isfinite(record.growth_rate):
            raise ValueError(f"{source}: {resource}: growth rate exceeds numeric range")
        records.append(record)
    if TOTAL_RESOURCE.casefold() not in seen:
        raise ValueError(f"{source}: missing {TOTAL_RESOURCE} total row")
    return _ordered_records(records)


def read_comparison(source: Path) -> list[ResourceProfit]:
    """Read the Comparison sheet without converting boolean cells to numbers."""
    frame = pd.read_excel(source, sheet_name="Comparison", dtype=object)
    return validate_comparison(frame, source)


def configure_style() -> None:
    """Require Times New Roman and retain editable text in the vector exports."""
    font_manager.findfont("Times New Roman", fallback_to_default=False)
    plt.rcParams.update(
        {
            "font.family": "Times New Roman",
            "font.size": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.linewidth": 0.7,
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
            "axes.unicode_minus": True,
        }
    )


def draw_panel(ax: plt.Axes, record: ResourceProfit, *, overview: bool) -> None:
    """Draw two dollar totals and growth, allowing zero and negative profits."""
    amounts = (record.telemetry, record.m2)
    low, high = min(0.0, *amounts), max(0.0, *amounts)
    span = high - low or 1.0
    ax.set_ylim(low - 0.18 * span if low < 0 else 0, high + 0.35 * span)
    ax.set_xlim(-0.6, 1.6)
    ax.set_axisbelow(True)
    ax.yaxis.grid(True, color="#E4E7EA", linewidth=0.5)
    ax.yaxis.set_major_locator(MaxNLocator(nbins=4))
    ax.yaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
    ax.bar((0, 1), amounts, width=0.52, color=COLORS, zorder=3)
    if low < 0:
        ax.axhline(0, color="#596571", linewidth=0.7, zorder=2)
    for x, amount in enumerate(amounts):
        ax.annotate(
            f"{amount:,.2f}",
            xy=(x, amount),
            xytext=(0, 5 if amount >= 0 else -5),
            textcoords="offset points",
            ha="center",
            va="bottom" if amount >= 0 else "top",
            fontsize=9 if overview else 10,
        )
    growth = record.growth_rate
    ax.text(
        0.5,
        0.975,
        "Growth: N/A" if growth is None else f"Growth: {growth:+.1%}",
        transform=ax.transAxes,
        ha="center",
        va="top",
        fontsize=10,
        color="#202A32",
    )
    title = "All Resources" if record.resource == TOTAL_RESOURCE else record.resource
    ax.set_title(title, fontsize=11 if overview else 12, pad=10)
    ax.set_ylabel("Profit ($)", fontsize=10 if overview else 11)
    ax.set_xticks((0, 1), CATEGORIES)
    ax.tick_params(axis="both", labelsize=9 if overview else 10, length=3)


def draw_resource(record: ResourceProfit) -> plt.Figure:
    """Create a 4.2 x 3.6 inch resource figure; the caller owns its lifetime."""
    configure_style()
    figure, ax = plt.subplots(figsize=(4.2, 3.6), layout="constrained")
    draw_panel(ax, record, overview=False)
    return figure


def draw_overview(records: list[ResourceProfit]) -> plt.Figure:
    """Create three columns with 4 x 3 inch panels and independent dollar axes."""
    if not records:
        raise ValueError("The overview requires at least one profit record")
    configure_style()
    rows = math.ceil(len(records) / GRID_COLUMNS)
    figure, axes = plt.subplots(
        rows, GRID_COLUMNS, figsize=(4 * GRID_COLUMNS, 3 * rows),
        layout="constrained", squeeze=False,
    )
    figure.get_layout_engine().set(w_pad=0.08, h_pad=0.08, wspace=0.03, hspace=0.04)
    for ax, record in zip(axes.flat, _ordered_records(records)):
        draw_panel(ax, record, overview=True)
    for ax in axes.flat[len(records):]:
        ax.set_visible(False)
    return figure


def export_figure(figure: plt.Figure, prefix: Path) -> None:
    """Export exact-size PNG/PDF/SVG, then close the figure even on failure."""
    try:
        figure.savefig(f"{prefix}.png", dpi=PNG_DPI, facecolor="white")
        figure.savefig(f"{prefix}.pdf", facecolor="white")
        figure.savefig(f"{prefix}.svg", facecolor="white")
    finally:
        plt.close(figure)


def main(argv: list[str] | None = None) -> int:
    """Export resource singles, the combined total, and the six-month overview."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, default=MICRO_RESULTS_DIR / "Comparison_Month.xlsx"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=MICRO_FIGS_DIR / "Six_Month_Comparison_1hour"
    )
    args = parser.parse_args(argv)
    try:
        records = read_comparison(args.input)
        configure_style()
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for record in records:
            filename = (
                TOTAL_FIGURE_NAME if record.resource == TOTAL_RESOURCE
                else f"Profit_Increment_{record.resource}"
            )
            export_figure(
                draw_resource(record),
                args.output_dir / filename,
            )
            print(f"Done [{record.resource}]", flush=True)
        export_figure(draw_overview(records), args.output_dir / OVERVIEW_NAME)
        print("Done [Overview]", flush=True)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    print(f"Exported {len(records) + 1} figures ({3 * (len(records) + 1)} files): {args.output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
