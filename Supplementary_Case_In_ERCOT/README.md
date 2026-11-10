# ERCOT Storage Dispatch and System Impact

Hourly backtesting of ERCOT Energy Storage Resources (ESRs), with historical operation comparisons, system-level post-processing, and reproducible figure scripts. The configured experiment covers **13 ESRs from January through June 2026**. Prepared input data cover 24 ESRs; the configuration selects the 13 used in the supplied results.

This distribution contains source code, prepared data, external reference data, results, and figures. Superseded explanatory notes are kept in [Archive](Archive/README.md). Manuscripts, writing drafts, peer-review packets, historical code archives, and local development files are excluded.

Rolling uses monthly operator-submitted SOC and HSL/LSL bounds, with initial energy reset to the first observed SOC of each month. See the [processing guide](Program/Micro/docs/ESS_Data_Processing_1hour_EN.md) for the implemented model and information assumptions.

## Repository layout

| Directory | Contents | Start here |
|---|---|---|
| [Data](Data/) | Monthly five-minute resource inputs and hourly system inputs | [Data guide](Data/README.md) |
| [Program](Program/) | Micro optimization, comparison tools, Macro post-processing, validation | [Program guide](Program/README.md) |
| [Results](Results/) | Supplied numerical outputs and reproducible random innovations | [Results guide](Results/README.md) |
| [Figs](Figs/) | Figure scripts, tests, and selected current exports | [Figure guide](Figs/README.md) |

All commands below run from this directory, the directory containing this README. Paths in the experiment configuration are resolved relative to the configuration file. Default script paths are anchored to the release directory, so this folder can be moved or downloaded without its original parent workspace.

## 1. Install the environment

Use **Python 3.12** with the pinned requirements. The source requires at least Python 3.11; validation for this distribution used Windows and Python 3.12.7. The pinned requirements record the installed validation environment; they are not a claim that every Python/platform combination has been tested.

PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\Activate.ps1
```

If script activation is disabled, invoke `.\.venv\Scripts\python.exe` wherever a command below uses `python`; changing execution policy is unnecessary.

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

The optimization requires `gurobipy` and a Gurobi license suitable for the model size and number of concurrent processes. Install/configure the license separately. A package installation alone does not establish license availability. For Macro post-processing, result inspection, and figure generation, install `requirements-core.txt` instead; these workflows do not solve optimization models. The Rolling validation entry point still imports `gurobipy`, even with `--validate-only`.

For the existing regression tests:

```bash
python -m pip install -r requirements-dev.txt
```

## 2. Validate the updated working tree

No solver or third-party Python packages are needed for the structural check:

```bash
python -B Program/tools/validate_release.py --skip-manifest
```

This checks required input/result coverage, Python syntax, English text content, JSON, workbook XML text, and saved random-file hashes. It reads result workbooks as well as source files. The [earlier packaging inventory](Archive/2026-11-10/RELEASE_MANIFEST.csv) is archived unchanged; it predates subsequent model, output, and documentation updates. There is no current `RELEASE_MANIFEST.csv` at the package root. Before a new publication, generate the complete inventory for the intended release and then validate without `--skip-manifest`. See [validation notes](Program/VALIDATION.md) for historical checks and the scope of the current documentation cleanup.

Validate regenerated Rolling results using the scientific result validator:

```bash
python -B Program/Micro/Experiment_Data_1hour.py --experiment Program/Micro/experiment_config_1hour.json --verify-results rolling
```

This checks all configured resource-month result frames against the submitted-SOC policy and writes verification JSON under `Results/Micro_Results/reports/`. Outputs generated under a different SOC policy must be regenerated before this check. It does not rerun optimization or prove the optimality of every saved solve.

## 3. Reproduce analysis from the supplied results

The supplied Rolling outputs allow the remaining analysis to run without a solver license:

```bash
python -B Program/Micro/classify_p_ess.py
python -B Program/Micro/Realized_Match.py
python -B Program/Micro/Compare_Month.py
python -B Program/Micro/Bridge.py --output-dir Results/Macro_Results/ESR_Output_Rolling
python -B Program/Macro/System_Rolling_Result.py
```

These commands replace their corresponding outputs. `Reality_Profit.py` can separately regenerate historical hourly power and energy-market revenue from the monthly input workbooks. The default Macro mode is gas displacement; coal and combined thermal modes and their output names are documented in [Program/Macro/README.md](Program/Macro/README.md).

Use the exact plot commands in [Figs/README.md](Figs/README.md) to regenerate the current figures. The Micro pipeline below generates classification, profit-comparison, example plots, and pooled Figure 4 a/b/c panels for all six months. Macro outputs and their Figure 4 d/e panels have separate entry points.

## 4. Rerun optimization

Start with one resource and month, using a separate dispatch output directory:

```bash
python -u -B Program/Micro/Rolling_Window_1hour.py --month 2026-06 --member BIG_STAR_ESR1 --validate-only --workers 1
python -u -B Program/Micro/Rolling_Window_1hour.py --month 2026-06 --member BIG_STAR_ESR1 --steps 3 --price-samples 7 --workers 1 --results-dir Results/smoke
```

The second command is a small execution check, using full-month source-derived parameters but only three executed hours and seven candidate bid prices. It does not reproduce the production experiment. Its dispatch files go to `Results/smoke/rolling/`; auxiliary reports and random files still use the configuration's `output_root`. For complete output isolation, copy the configuration and change `output_root`; see the [Micro run order](Program/Micro/RUN_ORDER.md#updating-an-experiment).

Run the complete configured Micro workflow:

```bash
python -u -B Program/Micro/run_pipeline.py --workers 4
```

The default is 12 worker processes, with one solver thread per process. Set `--workers` to suit memory, CPU capacity, and license limits. A full run performs many horizon optimizations and can be substantially more expensive than post-processing. No portable full-run runtime is asserted. Full-month Rolling verification runs immediately after optimization, before downstream analysis and figures. The runner stops on the first failed stage and records stage logs and `status.json` under `Results/Micro_Results/reports/pipeline_runs/`.

For individual stages, configuration options, output fields, and the optional Benchmark and Bridge tools, see [Micro run order](Program/Micro/RUN_ORDER.md) and [model/data details](Program/Micro/docs/ESS_Data_Processing_1hour_EN.md).

## Interpretation and reproducibility boundaries

- Power is MW, stored energy is MWh, price is USD/MWh, and energy-market revenue is USD. Positive resource power is discharge/export; negative power is charge/import.
- Local calendars use `America/Chicago`, while physical intervals are identified in UTC. March 2026 contains 743 modeled hours. Next-day tails support the forecast horizon and are excluded from monthly executed totals.
- Rolling uses current-month raw `min(Minimum SOC)` and `max(Maximum SOC)` for energy bounds, and resets initial energy to that month's first observed `State of Charge`. It does not integrate telemetry to estimate capacity. Power ratings use unrounded current-month `max(HSL)` and `min(LSL)`.
- The model uses synthetic forecasts made by perturbing historical future prices, historical availability, full-month submitted SOC/power extrema, and full-month price bounds. It is a retrospective backtest with these information assumptions.
- Rolling assumes one-way efficiency η = 0.95 (round trip 0.9025) and does not model standby drain. An efficiency-estimation program is not included in the current package; the configuration value is a model assumption.
- Revenue covers the implemented energy-market accounting. Ancillary-service revenue, degradation, capital costs, and network-constrained redispatch are outside this implementation.
- Macro results are counterfactual accounting based on the selected 13 ESRs and an assumed displaced fuel stack. They do not represent removal of all ERCOT storage or a system unit-commitment optimization.
- Prepared monthly inputs are included. The upstream acquisition/export programs and complete cost-stack construction provenance are not included. See the [Micro data guide](Data/Micro/README.md) and [Macro data guide](Data/Macro/README.md).
- Historical run manifests and the earlier packaging inventory do not certify the updated source or results. See [release notes](Program/RELEASE_NOTES.md) for the distinction and the current cleanup scope.

## Reuse status

The original ERCOT software code and associated documentation are licensed under the [MIT License](LICENSE), matching the repository-level code license. The included license copy retains the repository's copyright and permission notices.

The MIT License does not relicense third-party data or software. Data, derived data, results, and figures are not covered by MIT merely because they are included in this package; no separate CC0 or Creative Commons license is assigned to them here. Their reuse remains subject to applicable source-data terms and any file-specific notices. See the [prepared input guide](Data/README.md) for the included data and [release preparation notes](Program/RELEASE_NOTES.md) for packaging and version information.
