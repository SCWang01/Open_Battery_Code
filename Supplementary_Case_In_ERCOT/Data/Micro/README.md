# Micro Input Workbooks

`Six_Month_Data/<resource>_Usage_<YYYYMM>.xlsx` contains a `Usage` sheet with 202 fields. Files cover January through June 2026 for 24 ESRs. The active experiment uses the 13 resources whose `Telemetered Resource Status` is `ON` or `ONTEST` in more than 90% of current-month records in every month; they are listed in [experiment_config_1hour.json](../../Program/Micro/experiment_config_1hour.json).

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
| `State of Charge` | Stored energy in MWh; the first current-month observation initializes Rolling independently each month |
| `Minimum SOC`, `Maximum SOC` | Submitted stored-energy limits in MWh. Rolling fixes its lower energy bound at 0 MWh and uses the current-month maximum of `Maximum SOC` as its upper bound; `Minimum SOC` must still be finite and is kept for auditing |

Exact required curve fields and timestamp validation are defined in `Program/Micro/Monthly_Data_1hour.py`. Preserve original header names and status values when preparing replacement data.

Rolling requires finite current-month SOC values and an initial SOC between 0 MWh and the upper bound. It uses the original five-minute maximum without rounding or telemetry integration. A zero upper bound is valid and allows no charging or discharging, as in the LANTANA_ESR1 January input. `Capacity MWh` and `Usable Capacity MWh` in the output both equal the upper bound. The optional Benchmark retains its reconstructed telemetry-based energy envelope.

## Time range and missing data

Each file contains its local calendar month plus the entire first local day of the following month. This tail supports the 24-hour horizon. Adjacent monthly workbooks therefore overlap by one day. Deduplicate by resource, physical timestamp, and repeated-hour identity before concatenating source data.

The appended day does not contribute to current-month SOC or HSL/LSL extrema, initial energy, or executed profit.

Five-minute preparation retains the last actual SCED record in a slot. The model then independently averages valid power and price samples within physical UTC hours. Missing records and prices remain missing; they are not interpolated or filled with zero. `monthly_with_next_day_index.csv` records original rows, added-day rows, expected rows, gaps, missing prices, and the recorded source filenames.

UTOPIA_ESR1 begins on January 7 in the January input and is outside the active 13-resource selection. A missing March price observation and existing subhourly gaps remain in the prepared inputs. The shared loader excludes resource-months with missing full hours/days or insufficient hourly power/price coverage, and rejects malformed identities, timestamps, and numerical inputs. See the [processing guide](../../Program/Micro/docs/ESS_Data_Processing_1hour_EN.md) for precise rules.

Physical implementation hours are 744, 672, 743, 720, 744, and 720 for January through June, respectively. Local timestamps follow `America/Chicago`; the spring-forward hour is not fabricated.

## Source scope

The acquisition/export programs used to prepare these monthly workbooks are outside this distribution. The included index is a historical export record, not a command that can regenerate the workbooks. The [processing guide](../../Program/Micro/docs/ESS_Data_Processing_1hour_EN.md) describes the included preparation and loading rules; it does not supply the upstream acquisition workflow. The June workbook tail includes July 1 under the next-day rule described above. Recorded source filenames, node-mapping limitations, and tail provenance are consolidated in [Data/PROVENANCE.md](../PROVENANCE.md).
