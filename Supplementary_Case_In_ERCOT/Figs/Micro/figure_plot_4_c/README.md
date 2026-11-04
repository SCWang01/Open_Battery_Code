# Figure C: mean hourly state counts per ESR and Hub prices

The Python CLI defaults to **May 2026** and creates one Figure C from all
configured ESRs (currently 13). State observations come from their classified
Rolling workbooks through the shared Micro loader. The purple price profile
comes independently from the monthly ERCOT **HB_HUBAVG** hourly price source.

## Run

From the project root:

```powershell
python -B Figs/Micro/figure_plot_4_c/plot_hourly_state_counts_price.py
python -B Figs/Micro/figure_plot_4_c/plot_hourly_state_counts_price.py --month 202604
python -B Figs/Micro/figure_plot_4_c/export_hourly_state_counts.py --month 202604
python -B Figs/Micro/plot_esr_abc.py --month 202604
```

`--month YYYYMM` defaults to `202605` and selects one month with complete inputs.
The standalone c, compatibility `export_hourly_state_counts.py`, and unified
`plot_esr_abc.py` entries do not accept `--resource`: all configured ESRs are
always included. The unified entry writes one pooled a+b figure and one mean c
figure. The compatibility CLI uses this same c workflow without an intermediate
XLSX. Missing or invalid inputs stop processing rather than dropping resources.

## State-count meaning

For each local clock hour and state, sum observations across all configured
ESRs and divide by the **fixed configured resource count**, currently 13. Keep
fractional values without rounding or converting to percentages. Bars stack
**MC, CC, CD, NU, DC, DD, MD**, from bottom to top; maximum-rate charge and
discharge remain separate categories. The source is modeled `P_ESS_state`
classified from Rolling `Cleared MW`, not telemetry-match classification.

The left axis is **Mean count per ESR**. May uses 0–35 with ticks every 5;
stack totals range from approximately 30.615385 to 30.923077. Summing all 24
hourly mean-count stacks and multiplying by 13 gives the same 9,598 valid
resource-hour observations used for pooled b. State exclusions do not reduce
the resource denominator.

A blank state is excluded only when `Pcmax` or `Pdmax` is effectively zero,
using the classifier's existing tolerance. The shared monthly loader checks
that raw rows equal valid plotted states plus zero-limit exclusions; it rejects
NC and other unknown state labels. Duplicate hours, non-finite fields and
incomplete calendar coverage stop processing.

## Price profile and confidence interval

Read `Data/Macro/Price/Price_<Month>.xlsx`, sheet `Price`, field
`Official_Price`: the precomputed **HB_HUBAVG hourly mean (USD/MWh)**. For each
ERCOT local clock hour, average its daily observations across the selected
month. The band is the pointwise, two-sided **90% Student-t confidence interval
for the mean**, using sample standard deviation with `ddof=1`:

```text
mean +/- t(0.95, n - 1) * sample_SD(ddof=1) / sqrt(n)
```

`n` is the actual number of daily Hub samples for that clock hour, independent
of ESR number and state exclusions; it is never multiplied by 13. May 2026
supplies 31 observations per clock hour. At 09:00, the May mean is approximately
16.750968 and the interval is 13.172551–20.329385 USD/MWh.

The interval assumes approximate independence between daily samples. It
describes uncertainty in each clock-hour mean, not a price-percentile range,
a prediction interval, or a simultaneous confidence interval for the whole
curve. Negative prices are retained and the right axis covers the complete
interval.

An absent daylight-saving clock hour is not synthesized. A repeated local
clock hour must be distinguished by UTC, then averaged within the same local
day and clock hour before contributing one daily sample. Ambiguous repeats
without UTC fail validation.

## Output and style

The PNG and auditable source-data CSV are saved under
`Figs/Micro/figure_plot_4_c/Figs/` as:

```text
Fig_4_c_ALL_ESR_<YYYYMM>.png
Fig_4_c_ALL_ESR_<YYYYMM>_source_data.csv
```

Only **720 dpi PNG** is exported as an image. The figure keeps the existing 180 mm by
64 mm canvas, transparent background, Times New Roman typography, seven-state
red/gray/blue palette, purple diamond-marked price mean and hatched confidence
band. The CSV records pooled counts, mean counts per ESR, exclusions, and Hub
price sample sizes, means, and interval bounds with calculation precision.
The workflow writes no SVG, PDF or intermediate XLSX. Source workbooks are read
without modification.

The release includes the April and May all-resource exports. Historical
single-resource images and the legacy montage utility are excluded.

Requirements: Python with matplotlib, numpy, openpyxl and scipy; the shared
Micro loader supplies configuration, input validation, state aggregation and
Hub price statistics.
