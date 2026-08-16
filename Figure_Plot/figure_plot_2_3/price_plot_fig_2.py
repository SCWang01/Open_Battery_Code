# -*- coding: utf-8 -*-
"""
Created on Aug 14 2026
Price curves for the three price series used in Fig. 2 / Fig. 3.

Plots pri[0,:], pri[1,:], pri[2,:] (the three rows of settings.xlsx)
as three separate figures, 11 cm x 4.5 cm each.
"""

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.ticker import FormatStrFormatter
from openpyxl import load_workbook
from pathlib import Path

# ----- global style: Times New Roman -----
plt.rcParams['font.family'] = 'serif'
plt.rcParams['font.serif'] = ['Times New Roman', 'Times', 'DejaVu Serif']
plt.rcParams['font.size'] = 15

N_t = 24  # 24 hourly prices (hours 1..24)
Path("Figs").mkdir(exist_ok=True)
DATA_DIR = Path(__file__).resolve().parent

info = load_workbook(DATA_DIR / "settings.xlsx", data_only=True)
setting_table = info.worksheets[0]

Nsubfigs = 3
pri = np.zeros((Nsubfigs, N_t))
for i in range(Nsubfigs):
    for j in range(N_t):
        pri[i, j] = setting_table.cell(row=i + 1, column=j + 2).value

t = np.arange(1, N_t + 1)         # Time (Hour), 1..24
figsize = (11 / 2.54, 4.5 / 2.54)  # 11 cm x 4.5 cm

# (price data, ylabel, yticks) for figures 1..3
configs = [
    (pri[0, :],        'Price (CNY/kWh)', [0.25, 0.50, 0.75, 1.00]),
    (pri[1, :] / 1000, 'Price (USD/kWh)', [0.06, 0.07, 0.08]),
    (pri[2, :] / 1000, 'Price (USD/kWh)', [0.00, 0.02, 0.04]),
]

for idx, (price, ylabel, yticks) in enumerate(configs, start=1):
    fig, ax = plt.subplots(figsize=figsize)
    line = ax.plot(t, price, linewidth=3)
    # vertical dashed line from the t=1 point down to the x-axis
    ax.autoscale_view()
    ymin, _ = ax.get_ylim()
    ax.plot([1, 1], [ymin, price[0]], linestyle='--',
            color=line[0].get_color(), linewidth=1.5)
    ax.set_xlim(0, 25)
    ax.set_xticks([1, 5, 10, 15, 20, 25])
    ax.set_xlabel('Time (hours)')
    ax.set_ylabel(ylabel)
    ax.set_yticks(yticks)
    ax.yaxis.set_major_formatter(FormatStrFormatter('%.2f'))
    fig.savefig("Figs/price_%d.png" % idx, bbox_inches='tight',
                transparent=True, dpi=600)
    plt.close(fig)
