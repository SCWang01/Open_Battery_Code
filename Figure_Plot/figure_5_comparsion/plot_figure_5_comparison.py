"""Generate grouped-bar comparisons for prediction-error settings.

The script reads the bidding and self-scheduling summary workbooks for
meanstd = 2, 4, 6, 8, and 10. It uses the 36-month weighted rates from the
Annual Summary sheet and derives population variance and range from the
monthly profit_increment_k20 observations.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator
import numpy as np
import pandas as pd


# Keep text editable in SVG output.
plt.rcParams['font.family'] = 'sans-serif'
plt.rcParams['font.sans-serif'] = ['Arial', 'DejaVu Sans', 'Liberation Sans']
plt.rcParams['svg.fonttype'] = 'none'
plt.rcParams['pdf.fonttype'] = 42
plt.rcParams.update(
    {
        "font.size": 7.0,
        "axes.titlesize": 8.5,
        "axes.labelsize": 7.5,
        "axes.linewidth": 0.75,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "xtick.labelsize": 7.0,
        "ytick.labelsize": 7.0,
        "xtick.major.width": 0.7,
        "ytick.major.width": 0.7,
        "legend.fontsize": 6.5,
        "legend.frameon": False,
        "svg.fonttype": "none",
        "pdf.fonttype": 42,
    }
)


MEANSTD_VALUES = (2, 4, 6, 8, 10)
METHODS = ("Bidding", "Self-scheduling")
METHOD_FILE_LABELS = {
    "Bidding": "bidding",
    "Self-scheduling": "self_scheduling",
}
COLORS = {
    "Bidding": "#3E85C5",
    "Self-scheduling": "#FFA579",
}
OVERALL_PERIOD = "2023_1--2025_12"
EXPECTED_MONTHS = [str(year * 100 + month) for year in range(2023, 2026) for month in range(1, 13)]
FIGURE_SIZE_IN = (90 / 25.4, 72 / 25.4)


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parent
    default_input = root.parent.parent / "Uncertainties_Comparison"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=default_input)
    parser.add_argument("--output-dir", type=Path, default=root / "Figs")
    parser.add_argument("--dpi", type=int, default=600)
    return parser.parse_args()


def require_columns(frame: pd.DataFrame, columns: set[str], context: str) -> None:
    missing = columns.difference(frame.columns)
    if missing:
        raise ValueError(f"{context}: missing columns {sorted(missing)}")


def workbook_path(input_dir: Path, meanstd: int, method: str) -> Path:
    label = METHOD_FILE_LABELS[method]
    return input_dir / f"meanstd_{meanstd}" / f"summary_meanstd_{meanstd}_{label}.xlsx"


def read_workbook(path: Path, meanstd: int, method: str) -> tuple[dict[str, float], pd.DataFrame]:
    if not path.is_file():
        raise FileNotFoundError(f"Input workbook not found: {path}")

    annual = pd.read_excel(path, sheet_name="Annual Summary")
    monthly = pd.read_excel(path, sheet_name="Monthly Analysis")

    require_columns(
        annual,
        {
            "annual",
            "profit_increment_rate_k20",
            "cost reduction rate",
            "carbon reduction rate",
        },
        f"{path.name} / Annual Summary",
    )
    require_columns(
        monthly,
        {"Month", "profit_increment_k20"},
        f"{path.name} / Monthly Analysis",
    )

    overall = annual.loc[annual["annual"].astype(str) == OVERALL_PERIOD]
    if len(overall) != 1:
        raise ValueError(f"{path.name}: expected exactly one {OVERALL_PERIOD!r} row")

    month_labels = monthly["Month"].astype(int).astype(str).tolist()
    if month_labels != EXPECTED_MONTHS:
        raise ValueError(f"{path.name}: months must be consecutive from 202301 to 202512")
    if len(monthly) != 36:
        raise ValueError(f"{path.name}: expected 36 monthly rows, found {len(monthly)}")

    required_values = monthly["profit_increment_k20"].astype(float)
    if required_values.isna().any():
        raise ValueError(f"{path.name}: missing monthly profit_increment_k20 values")

    monthly_pp = required_values.to_numpy(dtype=float) * 100.0
    overall_row = overall.iloc[0]
    summary = {
        "meanstd_percent": meanstd,
        "method": method,
        "profit_increment_k20_percent": float(overall_row["profit_increment_rate_k20"]) * 100.0,
        "cost_reduction_percent": float(overall_row["cost reduction rate"]) * 100.0,
        "carbon_reduction_percent": float(overall_row["carbon reduction rate"]) * 100.0,
        "profit_increment_k20_minimum_percent": float(np.min(monthly_pp)),
        "profit_increment_k20_variance_pp2": float(np.var(monthly_pp, ddof=0)),
        "profit_increment_k20_range_pp": float(np.ptp(monthly_pp)),
    }
    if not all(np.isfinite(value) for key, value in summary.items() if key not in {"method"}):
        raise ValueError(f"{path.name}: non-finite derived summary value")

    monthly_source = pd.DataFrame(
        {
            "meanstd_percent": meanstd,
            "method": method,
            "month": month_labels,
            "profit_increment_k20_percent": monthly_pp,
        }
    )
    return summary, monthly_source


def load_data(input_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    summaries: list[dict[str, float]] = []
    monthly_frames: list[pd.DataFrame] = []
    for meanstd in MEANSTD_VALUES:
        for method in METHODS:
            summary, monthly = read_workbook(
                workbook_path(input_dir, meanstd, method), meanstd, method
            )
            summaries.append(summary)
            monthly_frames.append(monthly)

    summary_frame = pd.DataFrame(summaries)
    monthly_frame = pd.concat(monthly_frames, ignore_index=True)
    expected_summary_rows = len(MEANSTD_VALUES) * len(METHODS)
    expected_monthly_rows = expected_summary_rows * 36
    if len(summary_frame) != expected_summary_rows or len(monthly_frame) != expected_monthly_rows:
        raise AssertionError("Unexpected row count after loading validated workbooks")
    return summary_frame, monthly_frame


def grouped_values(summary: pd.DataFrame, metric: str, method: str) -> np.ndarray:
    subset = summary.loc[summary["method"] == method].set_index("meanstd_percent")
    values = subset.reindex(MEANSTD_VALUES)[metric]
    if values.isna().any():
        raise ValueError(f"Missing {metric} values for {method}")
    return values.to_numpy(dtype=float)


def plot_grouped_bar(
    summary: pd.DataFrame,
    metric: str,
    ylabel: str,
    title: str,
    output_stem: Path,
    dpi: int,
) -> None:
    fig, ax = plt.subplots(figsize=FIGURE_SIZE_IN, layout="constrained")
    x = np.arange(len(MEANSTD_VALUES), dtype=float)
    width = 0.36
    offsets = (-width / 2, width / 2)
    all_values: list[float] = []

    for method, offset in zip(METHODS, offsets, strict=True):
        values = grouped_values(summary, metric, method)
        all_values.extend(values.tolist())
        bars = ax.bar(
            x + offset,
            values,
            width=width,
            color=COLORS[method],
            edgecolor="#333333",
            linewidth=0.55,
            label=method,
            zorder=3,
        )
        label_padding = 8.0 if method == "Bidding" else 1.5
        ax.bar_label(
            bars,
            fmt="%.2f",
            padding=label_padding,
            fontsize=6.2,
            color="#272727",
        )

    y_max = max(all_values)
    ax.set_ylim(0, y_max * 1.25)
    ax.set_xticks(x, [str(value) for value in MEANSTD_VALUES])
    ax.set_xlabel("Prediction error (%)", labelpad=3)
    ax.set_ylabel(ylabel, labelpad=3)
    ax.set_title(title, pad=5, fontweight="semibold")
    ax.yaxis.set_major_locator(MaxNLocator(nbins=5, min_n_ticks=4))
    ax.grid(axis="y", color="#D9D9D9", linewidth=0.55, alpha=0.8, zorder=0)
    ax.tick_params(axis="both", length=3, color="#4D4D4D")
    ax.spines["left"].set_color("#4D4D4D")
    ax.spines["bottom"].set_color("#4D4D4D")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, 0.985), ncol=2, handlelength=1.4)

    output_stem.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_stem.with_suffix(".svg"), facecolor="white")
    if dpi != 600:
        raise ValueError("This figure contract requires 600-dpi PNG output")
    fig.savefig(output_stem.with_suffix(".png"), dpi=600, facecolor="white")
    plt.close(fig)


def write_source_data(summary: pd.DataFrame, monthly: pd.DataFrame, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary.to_csv(output_dir / "figure_5_summary_source_data.csv", index=False, float_format="%.10f")
    monthly.to_csv(output_dir / "profit_increment_k20_monthly_source_data.csv", index=False, float_format="%.10f")


def main() -> None:
    args = parse_args()
    summary, monthly = load_data(args.input_dir.resolve())
    write_source_data(summary, monthly, args.output_dir)

    figure_specs = (
        (
            "profit_increment_k20_percent",
            "Profit increment (%)",
            "Profit increment at 20% control",
            "profit_increment_k20",
        ),
        (
            "cost_reduction_percent",
            "Cost reduction (%)",
            "Cost reduction",
            "cost_reduction",
        ),
        (
            "carbon_reduction_percent",
            "Carbon reduction (%)",
            "Carbon reduction",
            "carbon_reduction",
        ),
        (
            "profit_increment_k20_minimum_percent",
            "36-month minimum (%)",
            "Minimum profit increment (20% control)",
            "profit_increment_k20_minimum",
        ),
        (
            "profit_increment_k20_variance_pp2",
            "Monthly variance (pp²)",
            "Profit-increment variance (20% control)",
            "profit_increment_k20_variance",
        ),
        (
            "profit_increment_k20_range_pp",
            "36-month range (pp)",
            "Profit-increment range (20% control)",
            "profit_increment_k20_range_36_months",
        ),
    )
    for metric, ylabel, title, filename in figure_specs:
        plot_grouped_bar(summary, metric, ylabel, title, args.output_dir / filename, args.dpi)

    print(f"Validated and loaded {len(summary)} method/error summaries.")
    print(f"Validated and loaded {len(monthly)} monthly observations.")
    print(
        f"Saved {len(figure_specs)} SVG and {len(figure_specs)} "
        f"{args.dpi}-dpi PNG figures to {args.output_dir.resolve()}."
    )


if __name__ == "__main__":
    main()
