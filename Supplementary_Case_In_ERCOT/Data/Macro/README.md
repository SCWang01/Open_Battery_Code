# Macro input data

This directory contains 42 prepared Excel workbooks: seven input families for
January-June 2026. They are the exact local inputs consumed by
[`System_Rolling_Result.py`](../../Program/Macro/System_Rolling_Result.py).
The release includes these prepared snapshots; the upstream acquisition and
fuel-stack construction pipeline is not included. File names alone do not
establish the provenance or redistribution terms of an upstream dataset.

## File and worksheet contracts

| Directory | Monthly file name | Worksheet | Required value columns |
| --- | --- | --- | --- |
| `ng_data/` | `ng_output_YYYYMM.xlsx` | `Output` | `NG_Output_MW` |
| `cg_data/` | `cg_output_YYYYMM.xlsx` | `Output` | `CG_Output_MW` |
| `Curtailment/` | `Curtailment_YYYYMM.xlsx` | `Curtailment` | `Wind Curtailment Proxy (MW)`, `Solar Curtailment Proxy (MW)`, `Total Curtailment Proxy (MW)` |
| `Price/` | `Price_January.xlsx` through `Price_June.xlsx` | `Price` | `Official_Price` |
| `ng_cost/` | `ERCOT_NG_Final_YYYY_MM.xlsx` | First worksheet (`Sheet1` in this snapshot) | Monthly supply-block fields below; fuel `NG` |
| `cg_cost/` | `ERCOT_CG_Final_YYYY_MM.xlsx` | First worksheet (`Sheet1` in this snapshot) | Monthly supply-block fields below; fuels `SUB`, `LIG` |
| `thermal_cost/` | `ERCOT_Thermal_Final_YYYY_MM.xlsx` | First worksheet (`Sheet1` in this snapshot) | Monthly supply-block fields below; fuels `NG`, `SUB`, `LIG` |

## Hourly series

All four hourly families require `Interval Start`, stored as an Excel datetime
in ERCOT local time (`America/Chicago`). Generation and curtailment values are
hourly mean MW; `Official_Price` is a Hub reference price in USD/MWh. The price
series is reported alongside the system results. It does not replace each
storage resource's node price in the Micro profit calculation.

The reader filters to the target local month and then requires the exact
ordered UTC-hour grid. Extra next-month forecast-tail records are excluded.
The expected in-month counts are:

| Month | Hours |
| --- | ---: |
| 2026-01 | 744 |
| 2026-02 | 672 |
| 2026-03 | 743 |
| 2026-04 | 720 |
| 2026-05 | 744 |
| 2026-06 | 720 |

March retains the daylight-saving spring-forward gap. An optional `Hour Start
UTC` column must agree with the local timestamps if present; it is required
to disambiguate repeated autumn hours when extending the input period. Missing,
duplicate, unordered or non-finite observations fail validation. Gas, coal and
curtailment must be nonnegative, and the wind plus solar proxy must equal the
total proxy within the numerical tolerance.

## Monthly supply blocks

The evaluator reads the first worksheet and requires:

| Column | Meaning and validation |
| --- | --- |
| `YEAR`, `MONTH` | Every row must match the file's requested month. |
| `capacity` | Positive block capacity in MW. |
| `$_per_mwh` | Nonnegative modeled fuel cost in USD/MWh. |
| `mmbtu_per_mwh` | Positive heat rate in MMBtu/MWh. |
| `carbon_t_per_mwh` | Positive CO2 intensity in tonnes/MWh, checked against heat rate and fuel. |
| `primary_fuel` | `NG`, `SUB` or `LIG`, restricted to the selected basis. Only legacy gas inputs may omit this column. |

Additional supplied fields include plant identifiers/names, prime mover,
generation and heat-input totals, average and nameplate capacity, minimum load,
monthly carbon/fuel-cost totals, and `block_id`. The current evaluator retains
the source workbook for provenance but uses only the required fields above.
In particular, it does not enforce the `Minimum Load (MW)` field.

Blocks are sorted stably by ascending `$_per_mwh`. Cost ties retain the original
row order, which also determines the corresponding emissions sequence. The
combustion factors encoded in the model are 0.05306, 0.09717 and 0.09772 tonnes
CO2/MMBtu for `NG`, `SUB` and `LIG`, respectively. They are model constants,
not values inferred anew from the input snapshot.

## Reuse

Keep the prepared workbooks unchanged when reproducing the bundled results.
Changing these inputs requires rerunning the selected Macro basis and its
figures. Each generated result records SHA-256 hashes of its contributing input
files. Use the [Macro program guide](../../Program/Macro/README.md) for the
counterfactual equations, output fields, CLI examples and limitations.
