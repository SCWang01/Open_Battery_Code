# Figure C: hourly P_ESS states and CAISO prices

This directory contains a two-stage, Python-only workflow for Figure C. It
pools the selected calendar months, draws hourly P_ESS-state counts as stacked
bars, and overlays average CAISO prices with two-sided 90% Student-t confidence
intervals.

## Select months

Edit SELECTION_YEARS and the 12-by-year SELECTION_MATRIX near the top of
export_hourly_state_counts.py. Rows represent January through December and
columns represent the configured years. A value of 1 selects a month.

The default matrix selects all months in 2025. Set PLOT_SEASONS to True to
ignore the custom matrix and generate Winter, Spring, Summer and Autumn across
every year in SELECTION_YEARS, matching plot_state_distribution_pies.py.

The plotting script keeps its historical May2025 filename for compatibility,
but its processing and output names are now period-generic.

## Direct source data

- State workbooks are read from Results/Bidding using the filename
  dsfunction_MonthYear_exact_V5_k20_classified_width.xlsx.
- Price files are read from Data/price using the filename
  YYYYMM CAISO Average Price.csv.
- No files are read from figure_plot_4_c/input.

Monthly price files contain an extra next-month day. The plotting script
filters every file to its target calendar month before aggregation, preventing
extra or duplicated dates from entering the mean or confidence interval.

## Run the workflow

From the Figure_Plot directory:

    python figure_plot_4_c/export_hourly_state_counts.py
    python figure_plot_4_c/plot_hourly_state_counts_price_May2025.py

The exporter creates the fixed intermediate workbook:

    output/P_ESS_state_hourly_counts_selected.xlsx

Its Selection worksheet is the single source of truth for the plotting script.
Custom mode contains one count worksheet; seasonal mode contains one for each
season. Do not edit the plotting script to repeat the month selection.

## State-count aggregation and exclusions

Hourly state rows are pooled directly across selected months; no per-month
normalization or averaging is applied. Longer months therefore contribute more
observations.

The plotted states, from bottom to top, are:

    MC, CC, CD, NU, DC, DD, MD

Rows with blank P_ESS_state are excluded only when Pcmax or Pdmax is effectively
zero and dsfunction_state_width is also blank. NC rows are excluded and
reported separately. Unknown non-empty state labels, malformed times, duplicate
times, missing columns and incomplete calendar months stop the exporter.

Every count worksheet and source-data CSV records, by hour:

- raw state rows;
- valid plotted state rows;
- excluded zero-limit rows; and
- excluded NC rows.

The left-axis limit is derived from the largest valid hourly stack, with about
10% headroom and readable integer ticks. It is not fixed to 31 days.

## Price aggregation and confidence interval

For each selected day and clock hour, the 12 five-minute prices are averaged
first. The resulting daily-hourly means are then pooled across every selected
calendar day for that clock hour.

For hour h:

    hourly_mean[h] = mean of all selected daily-hourly means at h

The band is the two-sided 90% Student-t confidence interval:

    hourly_mean[h] +/- t(0.95, n_days - 1) * sample_SD[h] / sqrt(n_days)

Days excluded from state counting remain in the price statistics. Each selected
day-hour must contain exactly 12 finite five-minute prices; incomplete,
duplicated or non-finite target-month data stop the plotting script. The price
axis dynamically covers the full confidence interval, including negative
prices, with about 10% headroom.

## Outputs

Figures are written to figures as transparent PNG (720 dpi), editable-text SVG
and vector PDF. Auditable source-data CSV files are written to output.

Output basenames match plot_state_distribution_pies.py:

    hourly_state_counts_and_price_2023_A_2024_B_2025_C

A, B and C are the numbers of selected months in each year. Recognized seasonal
selections add Winter, Spring, Summer or Autumn. Different custom matrices with
the same per-year counts may overwrite one another; rename outputs manually
when those variants must be retained.

The figure remains 180 mm by 64 mm and preserves the existing colors, stack
order, confidence-band styling and PNG/SVG/PDF export contract.

## Requirements

The workflow requires a recent Python 3 release with matplotlib, openpyxl and
scipy installed.
