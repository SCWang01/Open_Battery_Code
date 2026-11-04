# ERCOT Storage Dispatch and System Impact

Hourly backtesting of ERCOT Energy Storage Resources (ESRs), with historical operation comparisons, system-level post-processing, and reproducible figure scripts. The configured experiment covers **13 ESRs from January through June 2026**. Prepared input data cover 24 ESRs; the configuration selects the 13 used in the supplied results.

This distribution contains source code, prepared data, results, and figures. Manuscripts, writing drafts, peer-review packets, historical code archives, and local development files are excluded.

The complete distribution occupies approximately 577 MB (550 MiB), including the prepared data and saved results.

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

## 2. Inspect and validate the supplied release

No solver or third-party Python packages are needed for the file-integrity check:

```bash
python -B Program/tools/validate_release.py
```

This checks the release manifest, required input/result coverage, Python syntax, English text content, JSON, workbook XML text, and saved random-file hashes. Run it before regenerating results: changed generated files should no longer match the original release checksums. Use `--skip-manifest` after intentional changes. See [validation notes](Program/VALIDATION.md) for what was actually run during preparation.

Validate the supplied Rolling results using the scientific result validator:

```bash
python -B Program/Micro/Experiment_Data_1hour.py --experiment Program/Micro/experiment_config_1hour.json --verify-results rolling
```

This checks all configured resource-month result frames and writes verification JSON under `Results/Micro_Results/reports/`. It does not rerun optimization or prove the optimality of every saved solve.

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

Use the exact plot commands in [Figs/README.md](Figs/README.md) to regenerate the current figures. The Micro pipeline below generates classification, profit-comparison, and example plots; Figure 4 panels and Macro outputs have separate entry points.

## 4. Rerun optimization

Start with one resource and month, using a separate dispatch output directory:

```bash
python -u -B Program/Micro/Rolling_Window_1hour.py --month 2026-06 --member BIG_STAR_ESR1 --validate-only --workers 1
python -u -B Program/Micro/Rolling_Window_1hour.py --month 2026-06 --member BIG_STAR_ESR1 --steps 3 --price-samples 7 --workers 1 --results-dir Results/smoke
```

The second command is a small execution check, using full-month parameter reconstruction but only three executed hours and seven candidate bid prices. It does not reproduce the production experiment. Its dispatch files go to `Results/smoke/rolling/`; auxiliary reports and random files still use the configuration's `output_root`. For complete output isolation, copy the configuration and change `output_root`, as explained in the [Micro guide](Program/Micro/README.md).

Run the complete configured Micro workflow:

```bash
python -u -B Program/Micro/run_pipeline.py --workers 4
```

The default is 12 worker processes, with one solver thread per process. Set `--workers` to suit memory, CPU capacity, and license limits. A full run performs many horizon optimizations and can be substantially more expensive than post-processing. No portable full-run runtime is asserted. The runner stops on the first failed stage and records stage logs and `status.json` under `Results/Micro_Results/reports/pipeline_runs/`.

For individual stages, configuration options, output fields, and the optional Benchmark and Bridge tools, see [Micro run order](Program/Micro/RUN_ORDER.md) and [model/data details](Program/Micro/docs/ESS_Data_Processing_1hour_EN.md).

## Interpretation and reproducibility boundaries

- Power is MW, stored energy is MWh, price is USD/MWh, and energy-market revenue is USD. Positive resource power is discharge/export; negative power is charge/import.
- Local calendars use `America/Chicago`, while physical intervals are identified in UTC. March 2026 contains 743 modeled hours. Next-day tails support the forecast horizon and are excluded from monthly executed totals.
- The model uses synthetic forecasts made by perturbing historical future prices, historical availability, reconstructed monthly capacity, and full-month price bounds. It is a retrospective backtest with these information assumptions.
- Revenue covers the implemented energy-market accounting. Ancillary-service revenue, degradation, capital costs, and network-constrained redispatch are outside this implementation.
- Macro results are counterfactual accounting based on the selected 13 ESRs and an assumed displaced fuel stack. They do not represent removal of all ERCOT storage or a system unit-commitment optimization.
- Prepared monthly inputs are included. The upstream acquisition/export programs and complete cost-stack construction provenance are not included. See [data provenance](Data/PROVENANCE.md).
- Supplied historical manifests are labeled as historical records; path normalization and the English `Notes` header are documented in [release notes](Program/RELEASE_NOTES.md). They do not certify the edited release source against a historical source hash.

## Reuse status

No code license has been selected in this working project, so this prepared distribution does not assign one. Before public release, the owner must choose the code license and confirm redistribution terms for the included third-party data. Dataset provenance is documented without asserting ownership or inventing a license grant. See [release preparation notes](Program/RELEASE_NOTES.md).
