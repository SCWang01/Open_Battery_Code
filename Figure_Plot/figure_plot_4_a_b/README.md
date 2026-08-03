# figure_plot_412

This folder is a portable package for the May 2025 state-distribution figure.

## Contents

- `source_data/dsfunction_May2025_classified_final.xlsx`: original source workbook.
- `state_distribution_pies_May2025/`: plotting script, plotted source-data CSV,
  and the PNG/SVG/PDF figure outputs.

## Reproduce the figure

Required Python packages: `matplotlib` and `openpyxl`.

```powershell
python state_distribution_pies_May2025/plot_state_distribution_pies_May2025.py
```

The script resolves the workbook relative to this package and writes all updated
figure files beside the plotting script.
