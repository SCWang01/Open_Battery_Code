# ERCOT system monthly postprocessing

This stage converts the existing Micro dispatch into system-scale fuel-cost,
carbon, and curtailment counterfactuals. It performs no optimization. Use Python
3.11 or later with NumPy, pandas, openpyxl and pytz; install the release's
requirements before running. Gurobi is not needed for this stage.

Run from the release root (the directory containing `Data`, `Figs`, `Results`
and `Program`). The supplied results support postprocessing immediately. After
recomputing Micro results, refresh the comparison and Bridge aggregation first:

```powershell
python -B Program/Micro/Compare_Month.py
python -B Program/Micro/Bridge.py --output-dir Results/Macro_Results/ESR_Output_Rolling
```

Then run the desired basis:

```powershell
python -B Program/Macro/System_Rolling_Result.py
python -B Program/Macro/System_Rolling_Result.py --month 2026-05
python -B Program/Macro/System_Rolling_Result.py --basis coal
python -B Program/Macro/System_Rolling_Result.py --basis thermal
python -B -m unittest discover -s Program/Macro/tests -v
```

The default paths are resolved from the script location, so the program itself
can be invoked from another working directory. `--project-root PATH` selects a
complete alternative copy with the same layout. `--output-dir PATH` changes
only the output root; a relative output path is relative to the current working
directory. For example, this writes one thermal month to a separate directory:

```powershell
python -B Program/Macro/System_Rolling_Result.py --basis thermal --month 2026-05 --output-dir Results/Macro_Results/validation_run
```

That example creates
`Results/Macro_Results/validation_run/thermal/Rolling_Result_202605.xlsx`.
Existing files with the same names are replaced after validation. Close any
workbooks open in Excel before running. The CLI intentionally supports only
January-June 2026; extending the period requires updating `DEFAULT_MONTHS`, the
Micro configuration, and all corresponding input files together.

The default run validates January–June 2026 before replacing six
`Results/Macro_Results/System_Results/Rolling_Result_YYYYMM.xlsx` workbooks.
Each workbook contains `Rolling` hourly values and `Summary` monthly metrics,
units, methodology, and SHA256 fingerprints of every contributing source.
The tables contain computed research results; rerun the Python entry point to
refresh them when source data changes. Dependencies: pandas, NumPy, openpyxl,
and the existing Micro input/comparison helpers. No solver is required.

## Gas, coal and thermal modes

`--basis gas|coal|thermal` selects the responding generation pool; the default
is `gas`. The Python interface is `build_month(project_root, month, basis="gas")`.

| Basis | Observed baseline | Monthly cost directory | Fixed generation |
| --- | --- | --- | --- |
| gas | NG output | `ng_cost` | Coal |
| coal | Coal output | `cg_cost` | NG |
| thermal | NG + coal output | `thermal_cost` | Neither component |

All modes apply the same curtailment-first charging and generation-first
discharging balance to the same 13 ESRs. In thermal mode, Actual, Rolling and
WithoutESS all evaluate total thermal MW on the combined cost-ranked stack.
Its Actual cost/CO2 is a modeled pooled baseline, not the sum of independently
evaluated observed gas and coal. Observed component MW remain reference columns;
thermal mode does not report a modeled split as observed gas/coal dispatch.

Gas exports retain the original root directory and hourly field schema. Coal
and thermal exports go to `coal/` and `thermal/` under that root, also when
`--output-dir` specifies a custom root. Every file remains
`Rolling_Result_YYYYMM.xlsx`, with `Rolling` and `Summary` sheets. Publication
validates all requested temporary workbooks before replacing final files.

`CostStack.from_frame(frame, "YYYYMM", basis=...)` validates the selected fuel
set and uses each row's `primary_fuel` for combustion CO2:

| Fuel | t CO2/MMBtu |
| --- | ---: |
| NG (natural gas) | 0.05306 |
| SUB (subbituminous coal) | 0.09717 |
| LIG (lignite) | 0.09772 |

Source: [EPA 2025 GHG Emission Factors Hub, stationary combustion](https://www.epa.gov/system/files/documents/2025-01/ghg-emission-factors-hub-2025.pdf).
Workbook `carbon_t_per_mwh` must equal heat rate times the corresponding factor.
Unknown fuels, mode/fuel mismatches, missing coal/thermal fuel identifiers and
incorrect carbon intensities fail with context. `GasCostStack.from_frame` and
`GasEvaluation` remain compatible; legacy gas inputs may omit `primary_fuel`.

Coal uses `P_coal*`, `marginal_price_coal*`, `coal_excess_capacity_mw*` and
`coal_extrapolated*`; thermal uses the corresponding `P_thermal*` and `thermal`
fields. `cost*` and `carbon*` cover only the selected pool. Summary savings and
reductions are Actual minus the scenario, so negative values indicate an
increase. Compare percentage changes against each basis's own Actual totals;
calculate multi-month rates from summed numerators and denominators, never by
averaging monthly percentages. Zero denominators should be reported as `n.a.`.

All modes retain final-block capacity extrapolation, source fingerprints, UTC
hour coverage and unchanged Micro profits/terminal values. These are static
fuel-stack estimates without commitment, startup, ramping or minimum-output
constraints. The sections below document the backward-compatible gas default.

## Inputs and cohort

- `Data/Macro/ng_data`: official system Gas + Gas-CC hourly mean MW.
- `Data/Macro/cg_data`: observed coal MW, held fixed in all scenarios.
- `Data/Macro/Curtailment`: existing wind/solar curtailment proxies, MW.
- `Data/Macro/Price`: `Official_Price` Hub reference, USD/MWh.
- `Data/Macro/ng_cost`: monthly `ERCOT_NG_Final_YYYY_MM.xlsx` supply blocks.
- `Results/Macro_Results/ESR_Output_Rolling`: Bridge totals and member MW.
- Current Micro Rolling, Realized_Profit, Usage SOC, and
  `Comparison_Month.xlsx` for the 13 configured ESRs.

The cohort comes from `Program/Micro/experiment_config_1hour.json`, whose
current control share is 1. `Without selected 13 ESRs` removes only this cohort,
not all ERCOT storage. Raw `Telemetered Net Output` is the observed ESR baseline;
`Cleared MW` is the existing Rolling strategy. No new optimization is performed.
Bridge dispatch is verified against every member. All six income fields are
recomputed per member and checked against the existing monthly comparison.
The postprocessor reads Micro workbooks without modifying them.

## Balance and exact cost

For observed gas G, curtailment proxy C, selected ESR actual output A, and target
output M, use d=M−A. Additional discharge reduces gas first; surplus beyond gas
increases curtailment. Additional charging absorbs available curtailed energy
first; remaining demand increases gas. The removal scenario sets M=0 and uses
the same rule. Every hour satisfies ΔG−ΔC+d=0. Renewable absorption is the
signed difference C−C_new and can be negative.

`cost_calculation.py` uses stable ascending `$_per_mwh` order and piecewise
capacity filling, matching the legacy exact mode. A capacity breakpoint takes
the cheaper block's marginal price; output above total stack capacity
extrapolates the final block's cost and carbon intensity. The hourly
`gas_excess_capacity_mw*` and `gas_extrapolated*` fields and monthly statistics
expose extrapolation. Inspect the regenerated monthly flags and counts for the
selected dispatch snapshot. NG CO2 uses heat rate × 0.05306 t CO2/MMBtu in the
same cost order. Cost and carbon are model estimates for NG only, without unit
commitment, ramping, startup costs, coal redispatch, or network constraints.

## Result fields and profit

No suffix means Rolling; `_actual` means observed; `_withoutESS` means removal
of the selected 13 ESRs. `P_natural_gas*` and `curtailment_proxy*` are hourly mean
MW. `cost*` is modeled NG fuel USD per one-hour interval, `carbon*` is NG tonnes
CO2 per interval, and `marginal_price_gas*` is USD/MWh. Hourly `profit` and
`profit_actual` retain the sum of the original Micro trade settlements, including
existing realized imputation. The reference Hub price does not recalculate them.

Monthly profit preserves Micro's trading income plus terminal valuation.
Rolling uses each member's final `Modeled SOC After` times its own monthly mean
hourly node LMP. Actual uses its last in-month raw five-minute SOC times its
monthly mean Realized hourly node LMP. No initial inventory deduction or
additional efficiency adjustment is introduced. ESS income and NG fuel-cost savings are separate
metrics. Income and terminal value for the removal scenario are `n.a.`.

Dates are Excel date values: local `Interval Start` is America/Chicago;
`Hour Start UTC` is explicitly UTC stored without timezone metadata for Excel.
Only the target month is exported. Next-month first-day forecast tails are
excluded, and the missing March spring-forward hour is preserved. Row counts
are 744, 672, 743, 720, 744, 720. Numeric values are not rounded on export.

All inputs must validate before export. Temporary workbooks are reopened and
compared cell by cell, and all input hashes are rechecked before publication.
Final files are replaced atomically per workbook only after all requested
temporary exports pass verification.

## Modules and tests

| File | Responsibility |
| --- | --- |
| `System_Rolling_Result.py` | Read system inputs, balance the counterfactuals, build summaries, and verify/export workbooks. |
| `system_micro_inputs.py` | Validate the cohort, complete hourly grids, physical Micro results, monthly income, and Bridge member contributions. |
| `cost_calculation.py` | Validate and evaluate the monthly gas, coal, or combined thermal merit stack. |
| `tests/` | Existing unit/regression tests for exact costs, balance identities, physical-hour handling, typed Excel round trips, and Micro input boundaries. |

The tests use synthetic fixtures and temporary output directories. One optional
comparison against an external historical cost module is skipped unless
`ERCOT_LEGACY_COST_MODULE` names that Python file. The self-contained tests still
check hand-calculated breakpoints, partial blocks, extrapolation, and mixed-fuel
emissions. Only configure that optional variable for code you trust: the test
imports and executes the referenced module.

`V5_Case_Study_Reference.py` is excluded from this release. It is an independent
historical optimization reference, imports the absent `Random_Generator`
module and cost functions that are not part of the current API, and is not
called by any released entry point.

## Troubleshooting and interpretation

- A missing file or worksheet error identifies the required input. Preserve the
  names and sheet/column contracts in [the Macro data guide](../../Data/Macro/README.md).
- A stale Bridge or income error means the aggregate results disagree with the
  member results. Recreate `Comparison_Month.xlsx` and the Bridge files from the
  same complete Micro run before retrying.
- A timestamp error indicates missing, duplicate, unordered, or inconsistent
  hours. Correct the source; do not insert the nonexistent spring-forward hour.
- An invalid cost-stack error identifies the month and offending field or row.
  Each workbook must contain only its named month, positive capacities and heat
  rates, nonnegative fuel prices, and consistent fuel-specific carbon factors.
- Capacity extrapolation is a modeled extension of the last block, not evidence
  that actual dispatch exceeded a physical system limit. Inspect its flags and
  monthly counts when interpreting results.

These estimates exclude unit commitment, startup costs, ramping, minimum-output
constraints, transmission constraints, and market-price feedback. A curtailment
proxy is an input assumption and should not be described as measured recoverable
energy. The removal scenario concerns only the selected 13 resources. Different
fuel bases are separate counterfactual assumptions and must be reported by name.
