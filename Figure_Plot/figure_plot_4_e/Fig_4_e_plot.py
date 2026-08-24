from pathlib import Path
import argparse

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATE = "2025-04-07"
N_HOURS = 24
PLOT_HOURS = np.arange(N_HOURS)

RESULTS_PATH = BASE_DIR/"input" / "April2025_eta95%_std2_exact_V5_k20.csv"
CURTAILMENT_PATH = BASE_DIR/"input" / "curtailment_202504.csv"
PRICE_PATH = BASE_DIR / "input" / "202504 CAISO Average Price.csv"
OUTPUT_PATH = BASE_DIR / "Figs" / "Fig_4_e.png"

FIGURE_WIDTH_MM = 180.0
FIGURE_HEIGHT_MM = 64.0
PNG_DPI = 720


def configure_matplotlib():
    """Configure typography to match the April 2025 hourly-state figure."""
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


def day_index_from_date(date):
    parsed_date = pd.to_datetime(date)
    if parsed_date.year != 2025 or parsed_date.month != 4:
        raise ValueError("This plot script is configured for April 2025 data")
    return parsed_date.day - 1


def read_day_results(date):
    results = pd.read_csv(RESULTS_PATH)
    required_columns = {"P_ESS_actual", "P_ESS_controlled"}
    missing_columns = required_columns.difference(results.columns)
    if missing_columns:
        raise ValueError(
            "Missing required result columns: " + ", ".join(sorted(missing_columns))
        )

    day_index = day_index_from_date(date)
    start = day_index * N_HOURS
    end = start + N_HOURS
    day_results = results.iloc[start:end].copy()

    if len(day_results) != N_HOURS:
        raise ValueError(f"Expected {N_HOURS} result rows for {date}, got {len(day_results)}")

    # Difference between the k-share of actual fleet output and the controlled
    # unit's cleared output: positive = controlled unit charged more than its
    # proportional share, negative = it charged less.  k=0.2 is the fleet
    # split ratio defined in V5_Case_Study.py.
    increment_charge = (
        day_results["P_ESS_actual"].to_numpy() * 0.2
        - day_results["P_ESS_controlled"].to_numpy()
    )
    return increment_charge


def read_day_curtailment(date):
    curtailment = pd.read_csv(CURTAILMENT_PATH)
    required_columns = {"Date", "total_curtailment_mwh"}
    missing_columns = required_columns.difference(curtailment.columns)
    if missing_columns:
        raise ValueError(
            "Missing required curtailment columns: " + ", ".join(sorted(missing_columns))
        )

    curtailment["Date"] = pd.to_datetime(curtailment["Date"])
    day_curtailment = curtailment[
        curtailment["Date"].dt.strftime("%Y-%m-%d") == date
    ].copy()
    day_curtailment = day_curtailment.sort_values("Date")

    if len(day_curtailment) != N_HOURS:
        raise ValueError(
            f"Expected {N_HOURS} hourly curtailment rows for {date}, "
            f"got {len(day_curtailment)}"
        )
    if not np.array_equal(day_curtailment["Date"].dt.hour.to_numpy(), PLOT_HOURS):
        raise ValueError(f"Curtailment timestamps for {date} are not aligned to hours 0-23")

    # Each MWh observation covers one hour, so dividing by 1 h gives the
    # corresponding hourly-average curtailed power in MW (same numeric value).
    return day_curtailment["total_curtailment_mwh"].to_numpy(dtype=float) / 1.0


def read_day_hourly_price(date):
    price = pd.read_csv(PRICE_PATH)
    price["date"] = pd.to_datetime(price["date"], format="%m/%d/%Y %I:%M:%S %p")
    day_price = price[price["date"].dt.strftime("%Y-%m-%d") == date].copy()

    if day_price.empty:
        raise ValueError(f"No price data found for {date}")

    hourly_price = (
        day_price.set_index("date")["price"]
        .resample("h")
        .mean()
        .reindex(pd.date_range(date, periods=N_HOURS, freq="h"))
        .to_numpy()
    )

    if len(hourly_price) != N_HOURS or np.isnan(hourly_price).any():
        raise ValueError(f"Expected {N_HOURS} complete hourly prices for {date}")

    return hourly_price


def plot_fig_4_e(date=DEFAULT_DATE, output_path=OUTPUT_PATH):
    hours = PLOT_HOURS
    increment_charge = read_day_results(date)
    actual_curtailment = read_day_curtailment(date)
    hourly_price = read_day_hourly_price(date)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    configure_matplotlib()
    mm_per_inch = 25.4
    fig, ax_power = plt.subplots(
        figsize=(FIGURE_WIDTH_MM / mm_per_inch, FIGURE_HEIGHT_MM / mm_per_inch),
        facecolor="none",
    )
    # Reserve enough space for the left tick labels and the vertical Power label.
    fig.subplots_adjust(left=0.10, right=0.905, bottom=0.22, top=0.82)
    ax_price = ax_power.twinx()
    fig.patch.set_alpha(0.0)
    ax_power.patch.set_alpha(0.0)
    ax_price.patch.set_alpha(0.0)

    bar_width = 0.20
    increment_bars = ax_power.bar(
        hours - bar_width / 2,
        increment_charge,
        width=bar_width,
        color="#008000",
        label="Increment of charging power",
    )
    curtailment_bars = ax_power.bar(
        hours + bar_width / 2,
        actual_curtailment,
        width=bar_width,
        color="#9467bd",
        label="Actual curtailment",
    )
    (price_line,) = ax_price.plot(
        hours,
        hourly_price,
        color="#ff8c00",
        marker="o",
        linewidth=0.9,
        markersize=2.1,
        markeredgewidth=0,
        label="Price",
    )

    ax_power.axhline(0, color="black", linewidth=0.8)
    ax_power.set_xlabel("Time (hours)")
    ax_power.set_ylabel("Power (MW)", color="#1f4e79")
    ax_price.set_ylabel("Price (USD/MWh)")
    ax_power.set_xticks(np.arange(0, N_HOURS, 5))
    ax_power.set_xlim(-0.4, N_HOURS - 0.6)
    ax_power.grid(axis="y", linestyle=":", linewidth=0.6, alpha=0.45)

    # Keep a clear band inside the axes for the horizontal legend.
    power_bottom, power_top = ax_power.get_ylim()
    price_bottom, price_top = ax_price.get_ylim()
    ax_power.set_ylim(power_bottom, power_top + 0.22 * (power_top - power_bottom))
    ax_price.set_ylim(price_bottom, price_top + 0.22 * (price_top - price_bottom))

    ax_power.legend(
        [curtailment_bars, increment_bars, price_line],
        ["Actual curtailment", "Increment of charging power", "Price"],
        loc="upper center",
        bbox_to_anchor=(0.5, 0.99),
        ncol=3,
        frameon=True,
        facecolor="none",
        edgecolor="#d9d9d9",
        framealpha=1.0,
    )

    fig.savefig(output_path.with_suffix(".svg"), transparent=True)
    fig.savefig(output_path.with_suffix(".pdf"), transparent=True)
    fig.savefig(output_path, dpi=PNG_DPI, transparent=True)
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=DEFAULT_DATE)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()
    plot_fig_4_e(args.date, args.output)
