# figure_plot_4_a_b — May 2025 energy-storage state-distribution figure

A portable, reproducible Python package that turns the May 2025 hourly operating
record into a two-panel pie figure of energy-storage system states. Panel (a)
shows how interval width is distributed across charging/discharging states;
panel (b) shows how the hourly `P_ESS` values fall into those same states.

## Directory structure

```text
figure_plot_4_a_b/
├── README.md
├── source_data/                                    # data + preprocessing scripts
│   ├── dsfunction_May2025.xlsx                     # raw source workbook (sheet "May2025")
│   ├── classify_p_ess.py                           # raw → classified workbook
│   ├── add_dsfunction_state_array.py               # adds interval-width share array
│   └── dsfunction_May2025_classified_final.xlsx    # final workbook read by the plotter
└── state_distribution_pies_May2025/                # figure generation
    ├── plot_state_distribution_pies_May2025.py     # plotting script
    ├── state_distribution_pies_May2025_source_data.csv  # auditable figure-source table
    ├── state_distribution_pies_May2025.png         # 600 dpi, transparent
    ├── state_distribution_pies_May2025.svg         # editable vector (fonts as text)
    └── state_distribution_pies_May2025.pdf         # editable vector
```

## States

Each hourly record is assigned one of eight state labels; all seven that occur
in this dataset share one legend.

| State | Label                  |
|-------|------------------------|
| MC    | max-rate charge        |
| CC    | charge-for-charge      |
| CD    | charge-for-discharge   |
| NU    | null                   |
| DC    | discharge-for-charge   |
| DD    | discharge-for-discharge|
| MD    | max-rate discharge     |
| NC    | not classified         |

`NC` never occurs in the May 2025 data; the plotter refuses to run if it does.

## Figure overview

- **Panel (a)** — left pie. The hourly `dsfunction_state_share` arrays (8-state,
  per-row interval-width shares) are summed vertically. `MC` and `MD` are
  excluded and the remaining five states (`CC`, `CD`, `NU`, `DC`, `DD`) are
  renormalized to 100%.
- **Panel (b)** — right pie. Hourly `P_ESS_state` labels are counted across all
  legend states, so the share of every state (including `MC`/`MD`) is shown.

## Data pipeline

1. **Classify.** `source_data/classify_p_ess.py` reads the raw workbook
   (`dsfunction_May2025.xlsx`) and assigns each hourly `P_ESS` a state label by
   locating the value inside the row's `dsfunction` array. It writes the
   `P_ESS_state` column to the right of `P_ESS` and saves
   `dsfunction_May2025_classified.xlsx`, leaving the source file unchanged.
2. **Add the share array.** `source_data/add_dsfunction_state_array.py` appends
   the `dsfunction_state_share` column — the per-row, 8-state interval-width
   share array — to the classified workbook. The result is saved under the
   canonical name `dsfunction_May2025_classified_final.xlsx`, which is the
   workbook the plotter reads.
3. **Plot.** `state_distribution_pies_May2025/plot_state_distribution_pies_May2025.py`
   reads `source_data/dsfunction_May2025_classified_final.xlsx` (sheet
   `May2025`), validates it (exactly 744 records, vertical share sums and
   `P_ESS` counts reconcile, `NC` absent), writes the source-data CSV, and
   exports the PNG/SVG/PDF figure.

## Requirements

- Python 3.10+
- `matplotlib`
- `openpyxl`

## Reproduce

Run from the package root (the scripts resolve the workbook relative to the
package, so the whole folder can be moved as one reproducible unit):

```powershell
# 1. Classify P_ESS and write a classified workbook
python source_data/classify_p_ess.py

# 2. Add the interval-width share array to the classified workbook
python source_data/add_dsfunction_state_array.py

# 3. Reproduce the figure
python state_distribution_pies_May2025/plot_state_distribution_pies_May2025.py
```

All updated figure files and the source-data table are written beside the
plotting script.
