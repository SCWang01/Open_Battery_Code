# Hourly resource-level analysis

This directory contains the hourly ERCOT energy-storage backtest, realized-profit calculation, operating-state classification, and the bridge to system-level analysis. The default experiment covers 13 storage resources over January-June 2026 (78 resource-months). All source paths resolve relative to the code or configuration file, so the release can be placed in a different directory without editing absolute paths.

## Environment and first run

Use Python 3.11 or newer and install the release-root requirements. NumPy, pandas, and openpyxl support the calculations and workbook exports; gurobipy is imported by the Rolling entry points, and optimization requires a working Gurobi license. Matplotlib, SciPy, and the other root requirements support the figure stages. Git is optional: source-file and input hashes are still recorded when Git metadata is unavailable.

Run commands below from the release root, the directory containing `Data`, `Figs`, `Results`, and `Program`:

```powershell
python -m pip install -r requirements.txt
python -u -B Program/Micro/Rolling_Window_1hour.py --month 2026-06 --member BIG_STAR_ESR1 --workers 1 --validate-only
```

Input validation does not create a Gurobi model or solve an optimization problem, but the `gurobipy` package must be importable. It writes input-quality reports under `Results/Micro_Results/reports/`. The first run above validates one resource-month. To validate all configured inputs, omit `--month` and `--member`.

To reproduce the full Micro workflow:

```powershell
python -u -B Program/Micro/run_pipeline.py --workers 4
```

The default is 12 local worker processes. Lower `--workers` to fit available memory, CPU capacity, and Gurobi concurrent-session limits; each solver uses one thread. The complete six-month optimization is computationally intensive: each implementation hour evaluates 300 candidate prices with a 24-hour model horizon. No runtime estimate is provided because hardware and solver licensing differ.

The pipeline stops at the first failed stage. Its console prints the log directory, and each stage has its own log plus a shared `status.json` in `Results/Micro_Results/reports/pipeline_runs/<timestamp>/`. A rerun starts from the first stage and replaces same-named current outputs; it does not resume an interrupted solver run. See [RUN_ORDER.md](RUN_ORDER.md) to run stages individually or regenerate postprocessing from supplied results.

## Source map

| Module | Role |
|---|---|
| `project_paths.py` | Canonical release-relative data, result, and figure locations. |
| `experiment_config_1hour.json` | Resource registry and seeds, months, paths, forecast, solver, and physical assumptions. |
| `Experiment_Data_1hour.py` | Configuration validation, reconstructed energy parameters, persisted random matrices, provenance, and result checks. |
| `Monthly_Data_1hour.py` | Monthly workbook validation, physical-hour aggregation, availability flags, and input-quality reports. |
| `Rolling_Window_1hour.py` | Optimization model, bid construction and clearing, resource dispatch, exports, and CLI preflight. |
| `distributed_Rolling_Window_1hour.py` | Local multiprocessing runner. Each resource is solved in an independent process; the parent writes outputs. |
| `Reality_Profit.py` | Hourly telemetry and realized profit from paired five-minute power and price records. |
| `classify_p_ess.py` | Seven operating-state labels for Rolling execution. |
| `Realized_Match.py` | Signed normalized gaps between observed power and category power points. |
| `Compare_Month.py` | Per-month and six-month trading-profit and terminal-inventory comparisons. |
| `Bridge.py` | Align and sum the configured Rolling resources for Macro ESR output. |
| `Benchmark_1hour.py` | Optional submitted-bid Piecewise and Discrete benchmarks; excluded from the default pipeline. |
| `run_pipeline.py` | Fail-fast orchestration and per-stage logs. |

## Configuration and reproducibility

`experiment_config_1hour.json` uses schema version 3. Its relative `data_dir` and `output_root` are resolved against the configuration's directory, not the terminal directory. The registry selects 13 of the 24 resources included in the monthly input collection.

| Setting | Default and meaning |
|---|---|
| `months` | January-June 2026; months are modeled independently. |
| `interval_minutes` / `source_interval_minutes` | 60 / 5; physical hours are identified in UTC. |
| `timezone` | `America/Chicago` for local month and day boundaries. |
| `horizon_steps` | 24, including the current hour; monthly inputs support at most 25. |
| `price_samples` | 300 candidate current prices for constructing a bidding staircase, not 300 forecast scenarios. |
| `efficiency` | 0.95 in each direction; modeled round-trip efficiency is 0.9025. The implementation requires 0.95. |
| `meanstd_percent` | 2.0; first future-lead multiplicative price error has standard deviation 0.02. |
| `forecast_error_increment` | 0.001 added per later forecast lead. |
| `members` | Stable resource-to-seed mapping; each seed must be unique. |
| `solver_seed` / `solver_threads` | 0 / 1, enforced for this experiment. |
| `available_status` | An hour is available if any source status is `ON` or `ONTEST`, unless its entire local day has zero hourly telemetry. |
| `capacity_rounding_mwh` | Reconstructed monthly energy capacity rounds upward to 0.1 MWh. |
| `power_rounding_mw` | 0.1 for legacy telemetry ratings; current Rolling instead uses unrounded raw monthly submitted HSL/LSL extrema. |

Forecast innovations use a resource-month seed derived from `SeedSequence([member_seed, year, month])` and `Generator(MT19937(seed))`. Each `744 x 36` float64 array and its SHA-256 manifest live in `Results/Micro_Results/random/`. Existing arrays are checked and reused; an incomplete pair or identity/hash mismatch stops the run. Retain these supplied pairs for reproduction. Changing seeds or the resource registry requires a separate results root so stale pairs cannot be mistaken for the new experiment.

CLI selectors use `--member NAME` (repeatable) and `--month YYYY-MM` for Rolling and realized profit. Matching uses `--resource NAME` instead. `--horizon`, `--price-samples`, and `--steps` are useful for exploratory Rolling runs. Reduced runs must use a separate `--results-dir`:

```powershell
python -u -B Program/Micro/distributed_Rolling_Window_1hour.py --member BIG_STAR_ESR1 --month 2026-06 --steps 3 --price-samples 7 --workers 1 --results-dir Results/Micro_Smoke
```

This writes detailed Rolling outputs below `Results/Micro_Smoke/rolling/`. It still derives parameters from the complete month and uses the configured shared random matrices. Input-quality reports and those random matrices remain under the configuration's `output_root`; `--results-dir` does not isolate every auxiliary file. To isolate all outputs, copy the configuration inside `Program/Micro/`, change its `output_root` to `../../Results/Micro_Smoke`, and pass it with `--experiment` to individual stages. The one-command pipeline always uses the adjacent default configuration.

## Data and interpretation

Monthly inputs are `<resource>_Usage_<YYYYMM>.xlsx`, worksheet `Usage`. Each includes the current local month and the first local day of the following month for the forecast horizon. Five-minute rows are aggregated by UTC hour; March 2026 contains 743 physical hours. Required identity, time, price, telemetry, bid-curve, and submitted-limit fields are documented in the [data-processing guide](docs/ESS_Data_Processing_1hour_EN.md). The [monthly data note](docs/README_monthly_with_next_day.md) describes preparation and known gaps.

Rolling uses positive net power for discharge/export and negative net power for charge/import. `Pcmax` is exported as a nonpositive charging bound; solver charging variables use its positive magnitude. Columns containing modeled SOC are energy in MWh unless explicitly named `Normalized SOC`. The backtest reconstructs capacity and initial energy from full-month telemetry and uses historical future prices, future availability, and full-month price bounds. It therefore does not represent an online forecast available at dispatch time.

Realized `Profit` sums paired five-minute price-times-power contributions. Missing paired slots are filled using the same hour's mean paired contribution; `Observed Profit`, sample counts, and coverage retain that distinction. This generally differs from multiplying independently averaged hourly price and telemetry. Rolling `Profit` uses hourly cleared power times hourly mean price.

`Compare_Month.py` reports trading profit separately from terminal inventory value. It sums the final stored-energy value of **each month**, without deducting initial inventory or applying an efficiency adjustment. `Sum_Realized` and `Sum_Rolling` add trading profit and those inventory values. They are not a lifecycle economic metric and do not include degradation costs.

The matching workbook's explanatory column is named `Notes` in this English release. Category columns `MC`, `CC`, `CD`, `NU`, `DC`, `DD`, and `MD` contain signed normalized gaps, not probabilities. Values are based on telemetry clipped to the row's effective power bounds; `Original Telemetered Net Output` retains the unclipped hourly observation.

## Common failures

| Failure | Resolution |
|---|---|
| Missing Python package | Install the release-root requirements in the same interpreter used to launch scripts. |
| Gurobi license or concurrent-session failure | Configure a valid license and reduce `--workers`; input validation and existing-result postprocessing can be run separately. |
| Missing monthly workbook or hour | Check the configured data directory and quality report; the pipeline does not manufacture missing hours. |
| Random-file pair or SHA-256 mismatch | Restore the supplied matching NPY/JSON pair, or use a separate output root for a deliberately changed experiment. |
| A figure or summary is missing inputs | Complete its upstream stages in [RUN_ORDER.md](RUN_ORDER.md). |
| Partial results from `--steps` fail full-month verification | Keep the smoke output separate and use a full-month run for the full-month verifier. |

Legacy five-minute implementations, migration scripts, and historical code snapshots are not part of this release.
