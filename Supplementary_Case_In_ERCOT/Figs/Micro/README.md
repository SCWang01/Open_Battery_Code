# Micro figures

Run from the release root. The included results can be plotted directly. To
recompute results, follow [the Micro run order](../../Program/Micro/RUN_ORDER.md).

## Classification and profit figures

| Command | Image directory | Required input |
| --- | --- | --- |
| `python -B Figs/Micro/plot_classification_all.py` | `Classification_All/` | All configured classified and matched workbooks |
| `python -B Figs/Micro/Profit_Increment.py` | `Six_Month_Comparison_1hour/` | `Results/Micro_Results/Comparison_Month.xlsx` |
| `python -B Figs/Micro/Classification_Test/plot_classification_example.py` | `Classification_Test/` | BIG_STAR_ESR1 classified and matched results for January-June 2026 |

The classification batch exports PNG and PDF for each resource and all resources
pooled. Its count tables, point coordinates, and audit JSON are written to
`Results/Micro_Results/figure_data/Classification_All/`. Use `--experiment`,
`--output-dir`, and `--data-output-dir` to override the experiment, image, and
audit paths. Profit plots export PNG, PDF, and SVG; use `--input` and
`--output-dir` to override their paths.

Audit directory paths inside the release are recorded relative to the release
root. Explicit output destinations outside the release retain absolute paths.

The pooled classification panel uses resource-hour counts, with no capacity
weighting or equal-resource weighting. Telemetry is assigned to the nearest
available category using the signed normalized deviations from `realized_match`.
A match requires the strict condition `abs(gap) < 0.03`. Ties prioritize NU, then
the category compatible with the sign of telemetry power, then the fixed state
order. Missing/duplicate time keys, missing sources, invalid numeric values,
and zero effective power ranges are excluded and counted in the audit. The
release workbook annotation column is `Notes`.

The shared implementation in `Classification_Test/plot_classification_example.py`
is required by the batch plotter. The folder also contains tests; it is not a
disposable output folder. Profile interpolation connects discrete category counts
for display and does not estimate a continuous power distribution. Signed
deviations remain unchanged, including observations beyond the displayed range.

The all-resource classification canvas is 14 by 9.2 inches with Times New Roman
typography. Its scatter layer uses 240 dpi when exported. The profit series uses
the existing trading-income and month-end inventory-value convention; see the
Micro calculation documentation before interpreting the increments.

## Pooled a+b and mean-ESR c

```powershell
python -B Figs/Micro/plot_esr_abc.py
python -B Figs/Micro/plot_esr_abc.py --month 202604
python -B Figs/Micro/figure_plot_4_a_b/plot_state_distribution_pies.py --month 202604
python -B Figs/Micro/figure_plot_4_c/plot_hourly_state_counts_price.py --month 202604
python -B Figs/Micro/figure_plot_4_c/export_hourly_state_counts.py --month 202604
```

`--month YYYYMM` defaults to `202605`. Each command uses all configured resources
(currently 13) and requires complete inputs for the chosen month. These entries
do not accept `--resource`. They use the classified Rolling workbooks directly,
filter to the ERCOT local calendar month, and exclude the next-month forecast
tail. Missing resources or malformed input stop the run.

The unified entry writes two transparent 720 dpi PNGs, each with a source-data CSV:

* `figure_plot_4_a_b/Figs/Fig_4_a_b_ALL_ESR_<YYYYMM>.png`
* `figure_plot_4_c/Figs/Fig_4_c_ALL_ESR_<YYYYMM>.png`

The a+b canvas is 180 by 70 mm. Panel a reconstructs the five price spans
CC/CD/NU/DC/DD from `dsfunction`, validates the eighth-column energy against each
row's `Applied Maximum SOC`, and excludes SOC-boundary and maximum-rate segments.
Boundary membership takes priority even for zero power or power directed into
the battery's interior: energy within the existing 0.001 MWh boundary band is
mB and contributes no area to the five retained categories. This energy band is
distinct from the power classification tolerance below.
Other segments use the state returned by `classify_p_ess` directly, including its
zero-power tolerance and no-first-boundary rules; the width calculation does not
reassign those states. Zero spans contribute zero area; negative spans fail
validation. It sums the retained spans across all ESRs before calculating shares.
Panel b pools the seven hourly `P_ESS_state` categories MC/CC/CD/NU/DC/DD/MD and
then calculates shares, excluding zero-power-limit rows under the existing
classifier rules. Neither panel averages percentages from individual resources.

Power classification uses a fixed inclusive 0.001 MW tolerance for both idle
and maximum-rate states, with idle checked first. Stair matching and metadata
comparisons retain their 1e-6 absolute tolerance. `Realized_Match.py` keeps its
separate 0.0005 MW input-bound validation and the telemetry hit criterion remains
strictly below 3% of the effective power span.

Each month's source-data CSV records the current raw rows, excluded rows, valid
state denominator, state counts and percentages. Refresh these figures after
classification; counts from an older dispatch snapshot must not be reused.

Panel c uses a 180 by 64 mm canvas. For each local clock hour and state it sums
counts across resources, then divides by the fixed configured resource count,
currently 13; exclusions do not alter that denominator. Fractional counts are
preserved. The independent price series is the HB_HUBAVG hourly mean in
`Data/Macro/Price/Price_<Month>.xlsx`, sheet `Price`, column `Official_Price`.
Its band is a pointwise two-sided 90% Student-t confidence interval across daily
Hub samples. It is not an ESR-level interval or a price percentile range.
See [Figure c details](figure_plot_4_c/README.md) for the formula and daylight-saving
handling.

## Tests

```powershell
python -B -m unittest discover -s Figs/Micro/Classification_Test -p "test_*.py" -v
```

The suite covers threshold and tie behavior, accounting of exclusions, pooled
state/span aggregation, the fixed resource denominator, complete calendar
coverage, and Hub price statistics. Current figure sources and tests use the
same shared loaders and classifiers.
