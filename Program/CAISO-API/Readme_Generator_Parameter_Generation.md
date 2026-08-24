# CAISO Gas Unit Monthly Parameter Generation (Month_Agg_Clear_V2)

> This `CAISO-API` directory turns two official data sources — **EIA-923** (generation & fuel data) and **EIA-860 / EIA-860M** (generator capacity information) — into **monthly operating and cost parameters for every natural-gas unit** in the California CAISO grid.
>
> The final products are `Month_Agg_Clear_V2/CAISO_NG_Final_YYYY_MM.xlsx`. Each file has one row per "unit × month" combination, containing the following key parameters:

| Parameter | Meaning | Source |
|---|---|---|
| `Netgen` | Monthly net generation (MWh) | EIA-923 Page 1 |
| `MMBtuPer_Unit` | Average heat content (MMBtu/mcf) | EIA-923 Page 1 |
| `Elec_MMBtu` | Electric fuel consumption heat (MMBtu) | EIA-923 Page 1 |
| `Quantity` / `Elec_Quantity` | Fuel consumed / electric fuel consumed (mcf) | EIA-923 Page 1 |
| `Mcf_per_MWh` | Gas consumed per MWh generated (mcf/MWh), **computed here** | `Cost_D_MWH.py` |
| `average_capacity` | Monthly average output (MW), **computed here** | `Cost_D_MWH.py` |
| `capacity` | Matched nameplate capacity (MW), **matched here** | `match_monthly.py` + `match_generator_capacity.py` |
| `Minimum Load (MW)` | Minimum technical output (MW), **matched here** | same as above |
| `$_per_mwh` | Marginal fuel cost ($/MWh), **computed here** | `calculate_final_monthly.py` |

---

## I. Overall Pipeline

```
EIA-923 raw workbooks (Data/EIA923_Schedules_2_3_4_5_*.xlsx)
   │ ① extract_caiso_ng.py        —— keep records for BA = CISO & fuel = NG
   ▼
Data/CAISO_NG_YYYY.xlsx            (sheets: Page1_GenFuel / Page4_Generator / Page5_FuelReceipts)
   │ ② split_caiso_ng_monthly.py   —— split the 12 monthly column groups into 12 monthly files
   ▼
Month_Agg/CAISO_NG_YYYY_MM.xlsx    (one row per unit per month, incl. Quantity/Netgen/MMBtuPer_Unit)
   │ ③ clear_monthly_mmbtu.py      —— drop rows where MMBtuPer_Unit is 0 or “.”
   ▼
Month_Agg_Clear/CAISO_NG_YYYY_MM.xlsx
   │ ④ Cost_D_MWH.py               —— add columns Mcf_per_MWh and average_capacity
   │ ⑤ match_monthly.py            —— add columns capacity and Minimum Load (from the capacity table)
   ▼
Month_Agg_Clear/CAISO_NG_YYYY_MM.xlsx  (now contains all unit parameters)
   │ ⑥ calculate_final_monthly.py  —— multiply by monthly gas price -> $_per_mwh, sort by cost ascending
   ▼
Month_Agg_Clear_V2/CAISO_NG_Final_YYYY_MM.xlsx   ★ FINAL OUTPUT
```

The "capacity–minimum-load matching table" consumed at step ⑤ is produced by the supporting script:

```
EIA-860 annual generator tables + EIA-860M (Data/3_1_Generator_Y20xx.xlsx, may_generator2026.xlsx)
   │ ⑧ match_generator_capacity.py —— match every (Plant ID, Prime Mover) key,
   │                                  serialize capacity/minimum-load arrays as JSON
   ▼
Generator_Info/CAISO_NG_Plant_Capacity_Minimum_Load_2023_01_to_2026_04.xlsx
   │ consumed by ⑤ match_monthly.py
```

---

## II. Main Pipeline Scripts: What Each Function Does

### ① `extract_caiso_ng.py` — Extract CAISO natural-gas data from EIA-923

Reads the raw EIA-923 annual workbook, keeps only records with **BA code = CISO and fuel = NG**, and writes one workbook with four sheets.

| Function | Purpose |
|---|---|
| `normalized(series)` | Strips and upper-cases series values for comparison against "CISO"/"NG"; never mutates the source data |
| `field_names(field)` | Returns every accepted alias for one canonical field (e.g. the BA-code column) |
| `find_header_row(file, sheet, required)` | Auto-locates the **header row within the first 15 rows** (its position differs across EIA release types) |
| `read_eia_page(file, sheet, required)` | Reads a sheet, detects and drops the repeated "Early release data" warning column A, and unifies column names |
| `require_columns(frame, sheet, cols)` | Validates that required columns exist; raises if any are missing |
| `format_output(writer)` | Applies header styling, freezes the top row, and auto-sizes column widths on every output sheet |
| `extract_workbook(source, output, year)` | Core routine: reads Pages 1/4/5 and applies the CISO+NG filters; Page 4 has no fuel code, so its rows are matched back by (Plant Id, Prime Mover) pairs found in the filtered Page 1 NG set; auto-detects the data year and writes a `Filter_Summary` worksheet |
| `find_source_file(data_dir, year)` | Uniquely identifies the EIA-923 source file for a given year inside `Data` |
| `process_year_range(...)` | Batch-processes 2023–2026, skipping already-handled 2024/2025 by default |
| `parse_args() / main()` | CLI entry; supports both single-file and batch modes |

### ② `split_caiso_ng_monthly.py` — Split into monthly files

EIA-923 Page 1 stores 12 months as separate column groups (e.g. `Quantity\nJanuary`, `Netgen\nJune`). This script pivots that wide table into **one narrow file per month**.

| Function | Purpose |
|---|---|
| `clean_header(column)` | Collapses multi-line column names (e.g. static columns such as `Balancing\nAuthority Code`) into single-line strings |
| `split_monthly(source, output_dir, sheet)` | Validates the file holds exactly one year; for each of the 12 months extracts the six metric columns `[Quantity, Elec_Quantity, MMBtuPer_Unit, Tot_MMBtu, Elec_MMBtu, Netgen]` plus the static columns, prepends `YEAR`/`MONTH`, and writes `Month_Agg/CAISO_NG_YYYY_MM.xlsx` |

### ③ `clear_monthly_mmbtu.py` — Clean invalid rows

In the raw EIA data, `MMBtuPer_Unit` (heat content) can be `0` or the placeholder `.`; such rows make later calculations meaningless and are dropped.

| Function | Purpose |
|---|---|
| `rows_to_remove(series)` | Flags the two kinds of bad values: the `.` string placeholder and numeric/string `0` |
| `clear_workbooks(input_dir, output_dir)` | Iterates every `CAISO_NG_*.xlsx` in `Month_Agg`, drops bad rows, and writes to `Month_Agg_Clear/` |

### ④ `Cost_D_MWH.py` — Compute `Mcf_per_MWh` and `average_capacity`

This is where the **two core parameters are produced**; the formulas come straight from the project requirements (see `Info.md`):

```
Mcf_per_MWh      = 1 / ( MMBtuPer_Unit × Netgen / Elec_MMBtu )
average_capacity = Netgen / (days in the actual month × 24)
```

| Function | Purpose |
|---|---|
| `calculate_mcf_per_mwh(frame)` | Applies the formula above; rows with any missing/non-numeric/zero input, or `Netgen <= 0`, are flagged for removal |
| `calculate_average_capacity(frame)` | First validates `YEAR`/`MONTH` as a single valid integer pair, then uses `calendar.monthrange` for the day count → hours; `Netgen / hours` gives average output |
| `write_excel_atomically(frame, file)` | **Atomic write**: saves to a temp file first, then `os.replace` over the target so a half-written file can never be left behind |
| `process_workbook(input, output)` | Combines the calculation and row removal, updating `Month_Agg_Clear` files in place |
| `process_monthly_files(input_dir, output_dir)` | Directory-level entry point called by `main()`: iterates every monthly file and updates them in place |

### ⑤ `match_monthly.py` — Match nameplate capacity & minimum load

Matches the capacity arrays from the matching table to every monthly row by (Plant Id, Reported Prime Mover), then computes an **equivalent nameplate capacity**.

| Function | Purpose |
|---|---|
| `normalize_plant_id(value)` | Normalizes plant IDs so `246`, `246.0`, and `'246'` all match each other |
| `normalize_prime_mover(value)` | Normalizes prime-mover codes (lowercase, stripped) |
| `parse_array(value)` | Parses a JSON-style array string from an Excel cell (e.g. `[16.7, 16.7, …]`); returns None if invalid |
| `first_array_value(value)` | Returns the first valid item of the array |
| `capacity_array_values(value)` | Converts every item to float; returns None if any item is invalid |
| `cumulative_capacity_for_average(capacity_values, average_capacity)` | **Equivalent-capacity algorithm**: sums the nameplate array cumulatively until it reaches ≥ the month's average output, then **adds one more unit** as reserve; returns the cumulative total |
| `load_capacity_lookups(capacity_file)` | Builds two lookups from the capacity table: an exact (Plant, Mover) lookup plus a plant-only fallback lookup for plants that appear once |
| `match_monthly_frame(frame, exact_lookup, unique_lookup)` | Matches row by row: exact key first, then the unique-plant fallback; writes `capacity`, and `Minimum Load` as the minimum-load array's `a[0]` |
| `write_excel_atomically(...)` | Atomic write, as above |
| `process_monthly_files(...)` | Iterates all monthly files and updates them in place |

> **Where does the capacity table come from?** See supporting script ⑧ below.

### ⑥ `calculate_final_monthly.py` — Compute cost & produce the final files

The last step. It reads the monthly gas price `Month_Agg_Clear/Fuel_Cost.xls` (one `$/Mcf` per month), multiplies it by each row's consumption rate to get the marginal fuel cost, then sorts by cost ascending:

```
$_per_mwh = Mcf_per_MWh × monthly gas price ($/Mcf)
```

| Function | Purpose |
|---|---|
| `parse_year_month(value)` | Parses and validates a `YYYY-MM` CLI argument |
| `iter_year_months(start, end)` | Yields every (year, month) pair in the inclusive range `[start, end]` |
| `read_monthly_fuel_costs(fuel_cost_file)` | Reads the legacy `.xls` price table with `xlrd` (skipping the first 3 rows), parses the date column, and builds a `{(year, month): $/Mcf}` dict; duplicate months raise an error |
| `find_cost_input_column(frame)` | Locates the `Mcf_per_MWh` column case-insensitively |
| `calculate_clean_and_sort(frame, fuel_cost)` | Multiplies by the gas price to get `$_per_mwh`; drops blank/negative rows; `sort_values` ascending by cost (mergesort keeps the original relative order of equal values) |
| `validate_monthly_identity(frame, year, month)` | Checks that the file's `YEAR`/`MONTH` fields match its filename, preventing mislabeled months |
| `process_range(start, end, input_dir, fuel_cost_file, output_dir)` | Main flow: verifies the gas price covers all months → computes month by month → writes `Month_Agg_Clear_V2/CAISO_NG_Final_YYYY_MM.xlsx` |
| `process_year(year, ...)` | Compatibility wrapper: processes January–December of one year |

---

## III. Supporting Scripts: Feeding the Matching Table

### ⑦ `extract_ca_operable.py` — Extract California operating units

The annual EIA generator file `Data/3_1_Generator_Y2025.xlsx` is large; this script streams it read-only and copies every row with `State = CA` (plus the intro rows before the header) verbatim into `Generator_Info/3_1_Generator_Y2025_Early_Release_CA_Operable.xlsx` (the default input consumed by ⑧ `match_generator_capacity.py`).

| Function | Purpose |
|---|---|
| `extract_ca_operable(input_path, output_path)` | Iterates the `Operable` sheet, locates the `State` column, copies `CA` rows, and returns the row count |

### ⑧ `match_generator_capacity.py` — Build the "capacity–minimum-load matching table"

For every (Plant ID, Prime Mover) combination that appears in the CAISO monthly files, it matches all generators of that plant/type in the EIA generator tables under the **same composite key**, storing capacities and minimum loads as JSON arrays.

| Function | Purpose |
|---|---|
| `normalize_header(value)` | Normalizes column names (spaces stripped, lowercased) for case/whitespace-insensitive matching |
| `find_header(rows, required, path)` | Finds the header row containing the required columns in a streamed row iterator; returns the header and column indexes |
| `read_operation_pairs(operation_path)` | Reads the (Plant ID, Prime Mover) list from a plant-operation workbook (optional entry point) |
| `read_monthly_operation_pairs(monthly_dir, start, end)` | **Default entry point**: iterates all monthly files from 2023-01 to 2026-04 and collects deduplicated (Plant, Mover) keys |
| `collect_generator_values(path, requested_keys, ...)` | Streams one EIA generator workbook, keeping only the requested keys; appends each matched row's capacity/minimum-load to the two arrays |
| `collect_annual_generator_values(paths, keys)` | Checks the 2023/2024/2025 annual files' `Operable` and `Retired and Canceled` sheets in order, appending every matching row within a year; each later year only fills keys missed in earlier years |
| `collect_supplemental_generator_values(path, keys)` | Backfills remaining misses from EIA-860M `may_generator2026.xlsx` (`Operating`/`Retired` sheets, NG rows only; that workbook has no minimum-load field, so `None` placeholders keep both arrays aligned) |
| `serialize_array(values)` | Serializes a capacity array as a JSON string into an Excel cell (e.g. `[16.7, 16.7, …]`; `[]` when unmatched) |
| `write_output(pairs, values, path)` | Writes the four-column matching table `Generator_Info/CAISO_NG_Plant_Capacity_Minimum_Load_2023_01_to_2026_04.xlsx` and tallies unmatched rows |
| `build_capacity_table_from_pairs(...)` | Orchestrates the whole matching flow and returns matching statistics |
| `build_capacity_table(...)` | Wrapper that reads a plant-operation workbook via `read_operation_pairs` and delegates to `build_capacity_table_from_pairs` |

### ⑨ `summarize_plant_operation_months.py` — Summarize unit operating months

A supporting statistic: for each (Plant ID, Prime Mover) combination, it counts which months between 2023-01 and 2025-12 it appears in, writing `CAISO_NG_Plant_Operation_2023_01_to_2025_12.xlsx`. Useful for observing unit commissioning/retirement dates.

| Function | Purpose |
|---|---|
| `collect_operation_months(folder)` | Reads files month by month; a set guarantees the same combination in one month is counted only once |
| `write_summary(operation_months, path)` | Writes the four-column summary; `Operation Time` is a comma-separated list of `YYYY_MM`, `Total Operation Time` is the month count |

### ⑩ Other files

| File | Description |
|---|---|
| `kmeans_mmbtu_cluster.py` | **Standalone experimental script, not on the main pipeline**: runs one-dimensional K-means clustering (default 5 clusters) on a single month's `MMBtuPer_Unit` values, output to `Cluster_Result/`, for heat-content-based grouping analysis |
| `Fule_Cost_Function_Generation.py` | Empty placeholder file (not implemented) |
| `add_mmbtu_column.py` | Legacy utility: adds a `mmbtu_per_mwh` (= `Elec_MMBtu` / `Netgen`) column to the `CAISO_NG_Final_*.xlsx` files in `data/ng_cost/` for 2023-2025; not part of the main `Month_Agg` pipeline |
| `Info.md` | Project notes: total MMBtu comes from EIA-923, capacity from EIA-860, plus the unit-conversion formulas |

---

## IV. Deriving Each Final Parameter (with a worked example)

Using the first row of `Month_Agg_Clear/CAISO_NG_2024_06.xlsx` (Humboldt Bay, plant 246, prime mover IC):

| Field | Value | Derivation |
|---|---|---|
| `MMBtuPer_Unit` | 1.045 | Raw EIA-923 heat content (MMBtu/mcf) |
| `Elec_MMBtu` | 235933 | EIA-923 electric fuel consumption heat (MMBtu) |
| `Netgen` | 27047.029 | EIA-923 net generation (MWh) |
| `Mcf_per_MWh` | **8.347** | `1 / (1.045 × 27047.029 / 235933) = 1/0.11980 = 8.347` |
| `average_capacity` | **37.565** | June 2024 has 30 days = 720 h; `27047.029 / 720 = 37.565 MW` |
| `capacity` | **66.8** | Capacity-table array `[16.7×10]`; cumulative 16.7→33.4→**50.1 ≥ 37.565**, then +1 unit (16.7) → `66.8 MW` |
| `Minimum Load (MW)` | **14.5** | Minimum-load array `a[0] = 14.5` |
| `$_per_mwh` | given by the final step | June-2024 gas price × 8.347; June-2024 price ≈ $3.x/Mcf |

> Worked example 2: Moss Landing (plant 260, CT), `average_capacity = 242.13`, capacity array `[233,233,233,233]` → cumulative 233→**466 ≥ 242.13**, then +1 unit (233) → **699 MW**, matching the file.

### Key formula summary

```
Mcf_per_MWh      = 1 / ( MMBtuPer_Unit × Netgen / Elec_MMBtu )        # mcf/MWh
average_capacity = Netgen / (days in month × 24)                       # MW
capacity         = cumulative sum of capacity array until ≥ average_capacity, +1 unit  # MW
Minimum Load     = minimum-load array[0]                               # MW
$_per_mwh        = Mcf_per_MWh × monthly gas price ($/Mcf)             # $/MWh
```

---

## V. Recommended Execution Order

```bash
# 1. Extract (only when first processing 2023 / 2026)
python extract_caiso_ng.py                          # -> Data/CAISO_NG_YYYY.xlsx

# 2. Split into months
python split_caiso_ng_monthly.py                    # -> Month_Agg/CAISO_NG_YYYY_MM.xlsx

# 3. Clean
python clear_monthly_mmbtu.py                       # -> Month_Agg_Clear/CAISO_NG_YYYY_MM.xlsx

# 4. Compute Mcf_per_MWh and average_capacity (in-place update of Month_Agg_Clear)
python Cost_D_MWH.py

# 4.5 Build the capacity matching table (first time or after source updates; running
#     extract_ca_operable.py first can speed it up)
python match_generator_capacity.py                  # -> Generator_Info/...Capacity_Minimum_Load...xlsx

# 5. Match capacity and Minimum Load (in-place update of Month_Agg_Clear)
python match_monthly.py

# 6. Compute cost and produce the final files
python calculate_final_monthly.py                   # -> Month_Agg_Clear_V2/CAISO_NG_Final_YYYY_MM.xlsx
```

> Note: `calculate_final_monthly.py` requires `Month_Agg_Clear/Fuel_Cost.xls` to have a price for every month being processed; the file is read with `xlrd`, so run `pip install xlrd` on first use.
