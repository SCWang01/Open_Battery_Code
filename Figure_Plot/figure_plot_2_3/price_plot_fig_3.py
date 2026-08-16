# -*- coding: utf-8 -*-
"""Plot the three price-series sets used by Fig3_d, Fig3_e and Fig3_f."""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import FormatStrFormatter
from openpyxl import load_workbook


# ----- global style: Times New Roman -----
plt.rcParams["font.family"] = "Times New Roman"
plt.rcParams["font.serif"] = ["Times New Roman"]
plt.rcParams["font.size"] = 14
plt.rcParams["axes.labelsize"] = 14
plt.rcParams["xtick.labelsize"] = 14
plt.rcParams["ytick.labelsize"] = 14
plt.rcParams["legend.fontsize"] = 14

N_t = 24
Path("Figs").mkdir(exist_ok=True)
DATA_DIR = Path(__file__).resolve().parent

info = load_workbook(DATA_DIR / "settings.xlsx", data_only=True)
setting_table = info.worksheets[0]

pri = np.zeros((3, N_t))
for i in range(3):
    for j in range(N_t):
        pri[i, j] = setting_table.cell(row=i + 1, column=j + 2).value

t = np.arange(1, N_t + 1)
figsize = (10 / 2.54, 5/ 2.54)

configs = [
    (
        "d",
        np.vstack((pri[0, :], pri[0, :] * 2, pri[0, :] + 2)),
        "Price (CNY/kWh)",
        [1, 2, 3],
        "%.0f",
    ),
    (
        "e",
        np.vstack((pri[1, :], pri[1, :] * 2, pri[1, :] + 80)) / 1000,
        "Price (USD/kWh)",
        [0.05, 0.10, 0.15],
        "%.2f",
    ),
    (
        "f",
        np.vstack((pri[2, :], pri[2, :] * 2, pri[2, :] + 80)) / 1000,
        "Price (USD/kWh)",
        [0.00, 0.05, 0.10],
        "%.2f",
    ),
]

for index, prices, ylabel, yticks, yformat in configs:
    fig, ax = plt.subplots(figsize=figsize)
    for price in prices:
        ax.plot(t, price, linewidth=3)

    # Draw a black dashed guide from the highest price at t=1 down to
    # the bottom of the current y-axis, matching Fig. 2 line thickness.
    ax.autoscale_view()
    ymin, _ = ax.get_ylim()
    highest_price_at_t1 = np.max(prices[:, 0])
    ax.plot(
        [1, 1],
        [ymin, highest_price_at_t1],
        linestyle="--",
        color="black",
        linewidth=1.5,
        zorder=1,
    )

    ax.set_xlim(0, 25)
    ax.set_xticks([1, 5, 10, 15, 20, 25])
    ax.set_xlabel("Time (hours)")
    ax.set_ylabel(ylabel)
    ax.set_yticks(yticks)
    ax.yaxis.set_major_formatter(FormatStrFormatter(yformat))

    fig.savefig(
        f"Figs/price_3_{index}.png",
        bbox_inches="tight",
        transparent=True,
        dpi=600,
    )
    plt.close(fig)
