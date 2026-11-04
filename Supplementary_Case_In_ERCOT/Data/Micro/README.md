# Micro Input Workbooks

`Six_Month_Data/<resource>_Usage_<YYYYMM>.xlsx` contains a `Usage` sheet with 202 fields. Files cover January through June 2026 for 24 ESRs. The active experiment uses 13 resources, listed in [experiment_config_1hour.json](../../Program/Micro/experiment_config_1hour.json).

## Important fields

| Field | Meaning |
|---|---|
| `Resource Name` | ERCOT resource identifier |
| `SCED Time Stamp` | Actual local SCED observation time |
| `Standard 5-Minute Time Stamp` | Local five-minute interval start |
| `SCED Timestamp UTC` | UTC timestamp used for physical interval identity |
| `Repeated Hour Flag` | Source repeated-hour marker, where present |
| `LMP` | Resource-node locational marginal price, USD/MWh |
| `Telemetered Net Output` | Net power, MW; discharge positive |
| `Telemetered Resource Status` | Source status; `NA` is a literal status, not a missing-value token |
| `HSL`, `LSL` | Submitted upper/lower power limits, MW |
| TPO curve fields | Historical offer curve fields validated by the shared loader |
| SOC fields | Original audit fields; the current model reconstructs its energy envelope from telemetry |

Exact required curve fields and timestamp validation are defined in `Program/Micro/Monthly_Data_1hour.py`. Preserve original header names and status values when preparing replacement data.

## Time range and missing data

Each file contains its local calendar month plus the entire first local day of the following month. This tail supports the 24-hour horizon. Adjacent monthly workbooks therefore overlap by one day. Deduplicate by resource, physical timestamp, and repeated-hour identity before concatenating source data.

Five-minute preparation retains the last actual SCED record in a slot. The model then independently averages valid power and price samples within physical UTC hours. Missing records and prices remain missing; they are not interpolated or filled with zero. `monthly_with_next_day_index.csv` records original rows, added-day rows, expected rows, gaps, missing prices, and the recorded source filenames.

UTOPIA_ESR1 begins on January 7 in the January input and is outside the active 13-resource selection. A missing March price observation and existing subhourly gaps remain in the prepared inputs. The shared loader excludes resource-months with missing full hours/days or insufficient hourly power/price coverage, and rejects malformed identities, timestamps, and numerical inputs. See the [processing guide](../../Program/Micro/docs/ESS_Data_Processing_1hour_EN.md) for precise rules.

Physical implementation hours are 744, 672, 743, 720, 744, and 720 for January through June, respectively. Local timestamps follow `America/Chicago`; the spring-forward hour is not fabricated.

## Source scope

The acquisition/export programs used to prepare these monthly workbooks are outside this distribution. The included index is a historical export record, not a command that can regenerate the workbooks. See [provenance](../PROVENANCE.md) for the recorded product identifiers, appended July 1 data, mapping limitation, and upstream reproducibility boundary.
