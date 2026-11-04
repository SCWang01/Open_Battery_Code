# Macro results

These prepared results cover January-June 2026 for the 13 resources configured
in `Program/Micro/experiment_config_1hour.json`. They can be inspected and
plotted without rerunning the optimization. The results are research estimates;
their methods and validation contracts are documented in the
[Macro program guide](../../Program/Macro/README.md).

## Files

| Location | Content |
| --- | --- |
| `ESR_Output_Rolling/ESR_Rolling_Output_YYYYMM.xlsx` | Six Micro Rolling fleet-aggregation workbooks. |
| `System_Results/Rolling_Result_YYYYMM.xlsx` | Six gas-response counterfactual workbooks. |
| `System_Results/coal/Rolling_Result_YYYYMM.xlsx` | Six coal-response counterfactual workbooks. |
| `System_Results/thermal/Rolling_Result_YYYYMM.xlsx` | Six combined gas/coal-response counterfactual workbooks. |
| `System_Results/Basis_Comparison_202601_202606.csv` | Supplied comparison snapshot across the three responding-pool assumptions. |

The Bridge workbook's `Output` worksheet retains UTC and ERCOT local hours,
total net `ESR_Output_MW`, month, and each member's MW contribution. Positive
power is discharge and negative power is charging.

Each system workbook contains `Rolling` hourly values and `Summary` monthly
totals, units, methodology, and source SHA-256 hashes. Hourly rows cover only
the selected local month, preserving the March spring-forward gap. The three
scenarios are observed `Actual`, optimized `Rolling`, and removal of only the
selected 13 resources. A column without a scenario suffix represents Rolling;
`_actual` represents Actual and `_withoutESS` represents cohort removal.

Gas mode holds coal fixed; coal mode holds gas fixed. Thermal mode uses a pooled
cost-ranked gas/coal stack for every scenario, including Actual, and reports
observed gas/coal output only as a reference. Its modeled Actual fuel cost and
CO2 are not the sum of separate gas-mode and coal-mode Actual estimates.

Cost and CO2 refer to the selected generation pool. Storage trading income and
month-end energy valuation preserve the Micro calculation and are separate
from fuel-cost savings. Stack extrapolation appears explicitly in hourly flags
and monthly statistics. Percentage changes must use each basis's own Actual
baseline; multi-month rates must be formed from summed amounts.

## Regeneration

From the release root, after completing Micro results and comparison:

```powershell
python -B Program/Micro/Bridge.py --output-dir Results/Macro_Results/ESR_Output_Rolling
python -B Program/Macro/System_Rolling_Result.py --basis gas
python -B Program/Macro/System_Rolling_Result.py --basis coal
python -B Program/Macro/System_Rolling_Result.py --basis thermal
```

Add `--month 2026-05` to regenerate a single system month. Existing target
workbooks are replaced after validation. A custom `--output-dir` preserves the
bundled snapshots. The supplied basis-comparison CSV is an auxiliary snapshot;
the system CLI regenerates the monthly Excel files only. For refreshed
comparisons, use the monthly `Summary` sheets as the authoritative outputs.

Historical source hashes inside supplied workbooks describe the inputs used
when those results were produced. English documentation and source-code
translation do not change numeric data, but a newly generated workbook may
have different packaging, timestamps and provenance text. Compare numerical
tables and source values rather than requiring byte-identical Excel archives.
