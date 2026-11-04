# Data Provenance and Reproduction Boundary

This note summarizes the acquisition records retained in the original working project's experiment report and the prepared files included here. It describes those recorded snapshots; it does not claim that an upstream service will return the same historical revision today. Acquisition caches, original full-system archives, API credentials, and downloader/exporter programs are outside this distribution.

## Resource telemetry and nodal prices

The recorded acquisition used ERCOT's `np3-965-er/60d_sced_esr_data` product for ESR disclosures and `np6-788-cd/lmp_node_zone_hub` for node prices. Prices were matched on actual SCED timestamps and repeated-hour flags before five-minute sampling. The last actual record in each five-minute slot was retained. The source's 198 disclosure fields and four derived/matched fields form the 202 columns in each `Usage` sheet.

The current resource-to-node mapping was taken from a September 2, 2026 snapshot. Successful historical price joins do not independently establish that every historical resource-to-node assignment remained unchanged throughout January-June. The original acquisition report explicitly records this unresolved historical mapping limitation.

The recorded acquisition filled a March 29 disclosure gap from official MIS archives, using keys absent from the API cache. A missing nodal price at March 24, 2026 05:55:18 remains missing. UTOPIA_ESR1's January records start on January 7; this resource is not in the configured 13-member experiment.

January-May tails use the next month's already prepared resource data. The June tail contains July 1 observations recorded as coming from `60d_ESR_Data_in_SCED-30-AUG-26.csv`, with prices from `ERCOT_ESR_SCED_LMP_20260601_20260706.csv`. The per-file [index](Micro/Six_Month_Data/monthly_with_next_day_index.csv) retains source filenames and export coverage counts. Those upstream filenames are provenance references, not additional files required to run the supplied prepared-data workflow.

## Hub reference price

The original acquisition record identifies the same [NP6-785-ER historical RTM Load Zone and Hub Prices](https://www.ercot.com/mp/data-products/data-product-details?id=NP6-785-ER) archive for the complete hourly price series, selecting `HB_HUBAVG`:

| Recorded property | Value |
|---|---|
| Archive friendly name | `RTMLZHBSPP_2026` |
| Document ID | `1279579209` |
| Publication time | September 27, 2026, 08:06:54 UTC-05 |
| Workbook inside archive | `rpt.00013061.0000000000000000.RTMLZHBSPP_2026.xlsx` |
| Original ZIP SHA-256 | `4443f1ee88f0181ebfac48d561ee2793de191697c5abbce66b31c677eb345dcb` |

Hourly `Official_Price` is the arithmetic mean of four consecutive official 15-minute SPP values. The monthly files include the following month's first day. These Hub prices are used for Macro reference fields and selected figures. The Micro optimization and resource revenue use their individual nodal LMPs.

## Wind/solar curtailment proxy

The recorded sources are [NP4-732-CD wind hourly actual/forecast reports](https://www.ercot.com/mp/data-products/data-product-details?id=NP4-732-CD) and [NP4-737-CD solar hourly actual/forecast reports](https://www.ercot.com/mp/data-products/data-product-details?id=NP4-737-CD). For each delivery hour, preparation selected the latest published record containing both actual system generation and actual High Sustained Limit (HSL).

```text
Wind proxy  = max(actual wind HSL - actual wind generation, 0)
Solar proxy = max(actual solar HSL - actual solar generation, 0)
Total proxy = Wind proxy + Solar proxy
```

Each component is clipped before summation. Values are hourly mean MW. This is a derived curtailment proxy; it does not identify the cause of each HSL-generation difference or establish that every difference could be absorbed by additional storage charging.

The acquisition record reports that local hours March 8, 2026 01:00 and April 3, 2026 08:00 were recovered from 12 consecutive actual five-minute intervals using NP4-733-CD and NP4-738-CD. HSL and generation were averaged separately before computing the proxy. This recovery used additional observations rather than interpolation. Original selected-record caches and recovery scripts are not included.

## Gas and coal output

The source record identifies the [ERCOT generation data page](https://www.ercot.com/gridinfo/generation) and the 2026 Fuel Mix workbook, retrieved October 3, 2026 at 02:30:54 UTC. The recorded source SHA-256 is `d3b52a7fe2dfea5ac33448e023191a15de161453781f9660a7b5b43c20578d7e`.

Coal uses the official `Coal` category. Gas is `Gas + Gas-CC`. Original 15-minute values represent MWh; four consecutive interval energies are summed and divided by one hour to obtain hourly mean MW. Averaging the four energy numbers directly would understate the hourly MW by a factor of four.

The saved source record labels January-June target data FINAL and the appended July 1 data INITIAL. The appended tail is excluded from the monthly Macro results. These labels describe the recorded snapshot rather than future upstream revisions.

## Fuel cost and emissions stacks

The 18 files in `Macro/ng_cost`, `Macro/cg_cost`, and `Macro/thermal_cost` are model input stacks containing capacity, heat rate, cost, and emissions intensity. They are not observed hourly ERCOT system costs. The complete raw inputs, extraction versions, and construction programs for these stacks are absent from this project, so raw-to-stack reproduction is not supported by this distribution.

The code validates stack ordering/fields, fuel membership, and carbon intensity against its implemented combustion factors. See [Macro implementation and factors](../Program/Macro/README.md). This checks internal consistency; it does not independently verify the original plant-level data or cost assumptions.

## What can be reproduced

- The prepared workbooks and saved random arrays are included, with release SHA-256 fingerprints.
- The code reproduces hourly loading, modeled dispatch (with a suitable solver license), realized revenue, classification/matching, monthly comparisons, three Macro bases, and the retained figures.
- Exact figure bytes may vary with fonts and library versions. Solver versions/settings and optimization degeneracy can also affect exact dispatch equality.
- Acquisition from ERCOT endpoints, historical node-mapping verification, and construction of the fuel stacks require upstream material not included here. No download command in this release claims to reconstruct those missing steps.

## Third-party data and licensing

Source organizations and product identifiers describe provenance. The release does not create a license grant for third-party data. The project owner must confirm redistribution terms and select a code license before publishing this prepared distribution.
