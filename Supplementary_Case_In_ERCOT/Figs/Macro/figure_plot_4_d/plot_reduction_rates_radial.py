"""Plot Figure 4d: six ERCOT monthly ESR/system improvement rates.

Run from the project root with
``python -B Figs/Macro/figure_plot_4_d/plot_reduction_rates_radial.py``.
The source is the six System_Results Summary sheets. Profit includes existing
Micro terminal valuation; gas cost and CO2 are model estimates versus Actual.
The gas entry retains its 250 x 90 mm canvas. Coal and thermal entry points
share this renderer with 300 x 110 mm canvases and their own labels/scales.
Only the combined transparent 720 dpi PNG is exported.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from pathlib import Path

import matplotlib as mpl

mpl.use("Agg")
import matplotlib.pyplot as plt

from plot_profit_increment_radial import (
    ANNULUS_INNER_RADIUS,
    ANNULUS_OUTER_RADIUS,
    ZERO_RADIUS,
    StudyRates,
    configure_matplotlib,
    draw_radial_panel,
    load_study_rates,
    validate_basis,
)


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parents[2]
DEFAULT_RESULTS_DIR = PROJECT_ROOT / "Results" / "Macro_Results" / "System_Results"
DEFAULT_OUTPUT_ROOT = BASE_DIR / "outputs" / "ERCOT"
OUTPUT_DIRECTORY = "combined_radial"
OUTPUT_STEM = "Fig_4_d_202601_202606"
FIGURE_WIDTH_MM = 250.0
FIGURE_HEIGHT_MM = 90.0
PNG_DPI = 720
GOLDEN_RATIO = (1.0 + 5.0**0.5) / 2.0


@dataclass(frozen=True)
class PanelSpec:
    """Collocate one metric's title, scale and inherited visual colors."""

    key: str
    title: str
    color: str
    annulus_color: str
    axis_max: float
    tick_interval: float


PANELS = (
    PanelSpec("profit", "Profit increment rate", "#4C8FD3", "#DCEAF7", 400.0, 100.0),
    PanelSpec(
        "carbon", "Carbon-emission reduction rate", "#55A868", "#DCEFE3", 1.2, 0.3
    ),
    PanelSpec(
        "cost", "Natural-gas fuel-cost reduction rate", "#E69F45", "#F9E6CF", 1.2, 0.3
    ),
)


@dataclass(frozen=True)
class FigureSpec:
    """One basis's visual contract; gas constants above remain API-compatible."""

    width_mm: float
    height_mm: float
    output_stem: str
    panels: tuple[PanelSpec, ...]
    bar_width_fraction: float = 0.78
    label_span_degrees: float = 20.0
    annulus_width_fraction: float = 1.0
    annulus_gap: float = ANNULUS_INNER_RADIUS - ZERO_RADIUS
    annulus_thickness: float = ANNULUS_OUTER_RADIUS - ANNULUS_INNER_RADIUS
    title_fontsize: float = 15.0


def _fuel_panels(
    label: str, axis_max: float, tick_interval: float
) -> tuple[PanelSpec, ...]:
    return (
        PANELS[0],
        replace(
            PANELS[1],
            title=f"{label} CO₂ reduction rate",
            axis_max=axis_max,
            tick_interval=tick_interval,
        ),
        replace(
            PANELS[2],
            title=f"{label} fuel-cost reduction rate",
            axis_max=axis_max,
            tick_interval=tick_interval,
        ),
    )


FIGURES = {
    "gas": FigureSpec(FIGURE_WIDTH_MM, FIGURE_HEIGHT_MM, OUTPUT_STEM, PANELS),
    "coal": FigureSpec(
        300.0, 110.0, "Fig_4_d_coal_202601_202606", _fuel_panels("Coal", 2.5, 0.5)
    ),
    "thermal": FigureSpec(
        300.0,
        110.0,
        "Fig_4_d_thermal_202601_202606",
        _fuel_panels("Thermal", 1.2, 0.3),
        bar_width_fraction=54.0 / 60.0,
        # Give the longer profit labels enough arc length, including MAR below.
        label_span_degrees=43.0,
        annulus_width_fraction=1.0,
        # Retain the established white gap while slightly widening the label band.
        annulus_gap=(ANNULUS_OUTER_RADIUS - ANNULUS_INNER_RADIUS) / GOLDEN_RATIO,
        annulus_thickness=0.28,
        title_fontsize=17.0,
    ),
}


def create_combined_figure(rates: StudyRates) -> mpl.figure.Figure:
    """Draw the validated basis on its declared canvas without rescaling data."""
    config = FIGURES[validate_basis(rates.basis)]
    configure_matplotlib()
    figure = plt.figure(
        figsize=(config.width_mm / 25.4, config.height_mm / 25.4),
        facecolor="none",
    )
    try:
        for index, spec in enumerate(config.panels):
            axis = figure.add_axes(
                [
                    index / len(config.panels) + 0.01,
                    0.045,
                    1 / len(config.panels) - 0.02,
                    0.825,
                ],
                projection="polar",
            )
            draw_radial_panel(
                axis,
                getattr(rates, spec.key),
                spec.title,
                spec.color,
                spec.annulus_color,
                spec.axis_max,
                spec.tick_interval,
                bar_width_fraction=config.bar_width_fraction,
                label_span_degrees=config.label_span_degrees,
                annulus_width_fraction=config.annulus_width_fraction,
                annulus_gap=config.annulus_gap,
                annulus_thickness=config.annulus_thickness,
                title_fontsize=config.title_fontsize,
            )
    except Exception:
        plt.close(figure)
        raise
    return figure


def export_figure(
    figure: mpl.figure.Figure, output_dir: Path, basis: str = "gas"
) -> Path:
    """Export one 720 dpi PNG at the declared physical size, without extra formats."""
    config = FIGURES[validate_basis(basis)]
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{config.output_stem}.png"
    figure.savefig(output_path, dpi=PNG_DPI, transparent=True)
    return output_path


def main(
    results_dir: Path | None = None,
    output_root: Path | None = None,
    *,
    basis: str = "gas",
) -> Path:
    """Validate six monthly summaries, print their rates and export the combined PNG."""
    basis = validate_basis(basis)
    subdirectory = "" if basis == "gas" else basis
    source = (
        Path(results_dir)
        if results_dir is not None
        else DEFAULT_RESULTS_DIR / subdirectory
    )
    destination = (
        Path(output_root)
        if output_root is not None
        else DEFAULT_OUTPUT_ROOT / subdirectory
    )
    rates = load_study_rates(source, basis=basis)
    figure = create_combined_figure(rates)
    try:
        output_path = export_figure(figure, destination / OUTPUT_DIRECTORY, basis=basis)
    finally:
        plt.close(figure)
    for spec in FIGURES[basis].panels:
        data = getattr(rates, spec.key)
        monthly = ", ".join(
            f"{month}={value:.6f}%"
            for month, value in zip(data.months, data.monthly_percent)
        )
        console_title = spec.title.replace("₂", "2")
        print(f"{console_title}: {monthly}; Overall={data.overall_percent:.6f}%")
    print(f"Created {output_path}")
    return output_path


def run_cli(basis: str = "gas", *, description: str | None = None) -> Path:
    """Shared arguments for the three independent script entry points.

    Explicit directories are used directly, without adding a fuel subdirectory.
    """
    validate_basis(basis)
    parser = argparse.ArgumentParser(description=description or __doc__)
    parser.add_argument(
        "--results-dir",
        type=Path,
        help="directory containing this basis's six monthly workbooks",
    )
    parser.add_argument(
        "--output-root", type=Path, help="root under which combined_radial/ is created"
    )
    arguments = parser.parse_args()
    return main(arguments.results_dir, arguments.output_root, basis=basis)


if __name__ == "__main__":
    run_cli()
