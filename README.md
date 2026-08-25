# CAISO Battery Energy Storage Bidding Case Study

This repository provides the data, code, model outputs, and figure source files for an academic case study of aggregated battery energy storage bidding in the California Independent System Operator (CAISO) market. The study compares historical fleet operation with a bidding-based operating strategy and evaluates battery profit, natural-gas generation cost, renewable-curtailment absorption, and modelled carbon-emission reduction.

> **Licence:** the original software code in this repository is available under the [MIT License](LICENSE). External data, third-party software, and their associated materials remain subject to their respective terms. See [Licence, data terms, and disclaimer](#licence-data-terms-and-disclaimer).

> **Documentation note:** this README reflects the authors' good-faith but necessarily subjective description of the repository. The experiments, data-processing workflows, and code have undergone multiple rounds of revision and adjustment. Although the README is updated after experiments, some descriptions may still differ from the current implementation or files. If any inconsistency is found, the actual code and repository contents take precedence.

## Repository contents

```text
<repo-root>/
|-- Program/
|   |-- V5_Case_Study.py        # Main V5 simulation and export workflow
|   |-- cost_calculation.py     # Natural-gas cost, marginal-price, and carbon-emission models
|   |-- analyze_summary.py      # Monthly and annual Excel analysis
|   |-- Random_Generator.py     # Study period and fixed price-error scenarios
|   `-- CAISO-API/              # EIA/CAISO gas-unit parameter pipeline
|-- data/
|   |-- battery_data/           # Historical CAISO battery output
|   |-- curtailment/            # Renewable-curtailment inputs
|   |-- ng_cost/                # Monthly natural-gas merit-order stacks
|   |-- ng_data/                # Hourly natural-gas generation
|   |-- price/                  # CAISO market-price inputs
|   |-- random_data/            # Fixed price-error scenarios (manifest + .npy)
|   |-- settings.xlsx           # Figure 2/3 price-sequence source data
|   |-- settings_sequence.xlsx  # Figure 1/S1 price-sequence source data
|   |-- lmp2023.npy             # Figure 2_q 2023 LMP source array
|   |-- tep2023.npy             # Figure 2_q 2023 temperature source array
|   `-- CAISO-historical-co2-20260720.csv  # Retained historical-emissions source
|-- Results/                    # Monthly arrays/CSVs, summaries, and analysis workbook
|-- Figure_Plot/
|   |-- Figure_Generation.py    # Figure 4 input synchronization and orchestration
|   |-- Figure_Generation_manifest.json  # Latest Figure 4 provenance record
|   `-- figure_*/               # Figure scripts, source snapshots, and rendered assets
|-- CITATION.cff                # Software creator and citation metadata
|-- DATA_AND_THIRD_PARTY_NOTICES.md  # Data rights and provider notices
|-- LICENSE                     # MIT licence for original software code
|-- RELEASE_CHECKLIST.md        # Internal pre-publication checklist
|-- requirements.txt            # Pinned reference Python environment
`-- README.md                   # Reproducibility-oriented project documentation
```

The core scripts resolve their data and result paths relative to the repository rather than the shell's current directory. Some legacy figure scripts use working-directory-relative paths; their commands are provided in [Figure source data and reproduction](#figure-source-data-and-reproduction).

The default study and committed scenario manifest cover January 2023 through December 2025. The data and preprocessing directories also retain some 2026 source files, but those later files are not included in the default model period or committed summary workbook.

## Requirements

Python 3.10 or later is required. The pinned reference environment in [`requirements.txt`](requirements.txt) corresponds to CPython 3.10.17, the interpreter recorded for the committed Figure 4 generation run.

### Core model and analysis

```text
gurobipy
numpy
pandas
openpyxl
tqdm
matplotlib
scipy
```

Install the pinned reference dependency set with:

```powershell
python -m pip install -r requirements.txt
```

The requirements file includes `gurobipy`, but the optimisation still requires a separately obtained valid Gurobi licence. Gurobi is not distributed or licensed through this repository. See [Gurobi Licensing](https://www.gurobi.com/product/licensing) and the [Gurobi Academic Program](https://www.gurobi.com/academics).

### Figure and preprocessing dependencies

`requirements.txt` also includes the figure and preprocessing dependencies. `matplotlib` and `scipy` support the figure workflows. `xlrd` is used by the gas-unit preprocessing pipeline. `scikit-learn` is used by `Program/CAISO-API/kmeans_mmbtu_cluster.py`, a standalone K-means heat-content clustering utility; it is not required for the main simulation or analysis scripts.

## Quick start

All commands below assume PowerShell and a current directory at `<repo-root>`.

### 1. Inspect the committed results

The repository already contains the complete default monthly outputs, summary CSV, analysis workbook, and rendered figure assets. Model inspection therefore does not require rerunning the optimisation.

Start with:

```text
Results/summary_202301_202512_exact_V5_k20.csv
Results/analysis_202301_202512.xlsx
Figure_Plot/
```

### 2. Regenerate the analysis workbook

```powershell
python Program/analyze_summary.py
```

The default input and output are:

```text
Results/summary_202301_202512_exact_V5_k20.csv
Results/analysis_202301_202512.xlsx
```

Custom paths can be supplied explicitly:

```powershell
python Program/analyze_summary.py Results/summary.csv `
  --output Results/analysis_custom.xlsx
```

The analyzer validates the required summary columns and includes only complete January–December calendar years; it fails if the input contains no complete year.

### 3. Run the May 2025 case

```powershell
python Program/V5_Case_Study.py
```

Running the file directly calls `run_may_2025()`. It writes the May 2025 result files, the single-month summary, and the hourly demand-supply function workbook to `Results/`.

The complete set written by this helper is:

```text
Results/May2025_eta95%_std2_exact_V5_k20.csv
Results/ncd_May2025_exact_V5_k20.npy
Results/Pcleared_May2025_exact_V5_k20.npy
Results/dsfunction_May2025_exact_V5_k20.xlsx
Results/summary_202505_exact_V5_k20.csv
Results/simultaneous_charge_discharge_counts_202505_exact_V5_k20.csv
```

The equivalent import-based command is:

```powershell
Push-Location Program
python -c "from V5_Case_Study import run_may_2025; print(run_may_2025())"
Pop-Location
```

The module also exposes `run_april_2025()` and `run_Jan_2025()`. Like the May helper, both export a demand-supply-function workbook and a one-month summary; they are import-based helpers rather than command-line options. Running `V5_Case_Study.py` directly always selects May 2025.

### 4. Run the complete January 2023–December 2025 study

```powershell
Push-Location Program
python -c "from V5_Case_Study import run_all_months; print(run_all_months())"
Pop-Location
```

This is the full-study entry point. It produces one monthly CSV plus `ncd_*.npy` and `Pcleared_*.npy` for each month. It then writes `summary_202301_202512_exact_V5_k20.csv` and `simultaneous_charge_discharge_counts_202301_202512_exact_V5_k20.csv` to `Results/`. The full-study path does not export monthly `dsfunction_*.xlsx` workbooks.

## Configuration

The model currently has no command-line interface or external configuration file. The study period and interval count are defined in `Program/Random_Generator.py` and imported by `Program/V5_Case_Study.py`:

```python
START_YEAR_MONTH = (2023, 1)
END_YEAR_MONTH = (2025, 12)
N_T = 24
```

The optimisation settings are defined near the top of `Program/V5_Case_Study.py`:

```python
eta = 0.95
N_price = 300
meanstd = 2
k = 0.2
COST_MODE = "exact"
```

Keep `k` in `(0, 1]`. `COST_MODE="exact"` uses the monthly piecewise merit-order data in `data/ng_cost/`. The retained `quadratic` compatibility mode requires `Program/Fuel_Coe.xlsx`, which is not included, so only `exact` runs end-to-end with the committed files.

The arrays and `manifest.json` in `data/random_data/` are strictly validated against `N_T`, `START_YEAR_MONTH`, and `END_YEAR_MONTH`. If those settings change, regenerate a complete matching scenario set with `Program/Random_Generator.py` and provide price, battery, curtailment, natural-gas, and natural-gas-cost inputs for every selected month. The generator refuses to overwrite the committed scenario files unless `--force` is supplied.

The `eta95%` fragment in monthly CSV filenames is currently hard-coded. Changing `eta` without also changing the naming logic can overwrite an existing file whose name still contains `eta95%`. Preserve required outputs before changing model parameters or file-naming code.

## Model workflow

`Program/V5_Case_Study.py` performs the following steps for each selected month (entry point `run_one_month`, batched by `run_all_months`):

1. load market price, historical battery output, renewable-curtailment, and natural-gas generation data — `readdata`, with the fixed price-error scenario loaded by `load_monthly_price_error_data` (`Program/Random_Generator.py`);
2. reconstruct the equivalent fleet capacity, power limits, and initial state of charge — `readdata` (returns `Cap`, `Pdmax`, `Pcmax`, `SINI`);
3. optimise the controllable `k` fraction over rolling horizons and construct stair bidding functions — `calculate_profit` → `biddingNEW` (Gurobi MILP, one solve per candidate price) → `build_dsfunction`;
4. combine the market-cleared controllable output with the historical passive fraction — `calculate_main` (`P_method = P_cleared_controlled + (1-k) * battery`);
5. calculate historical and counterfactual battery profit, natural-gas generation and cost, renewable-curtailment absorption, and modelled carbon reduction — `calculate_profit_actual` for the baselines and `calculate_cost_and_carbon` for the gas/curtailment/carbon deltas, with cost, marginal price, and carbon from `Program/cost_calculation.py`; and
6. export hourly arrays, monthly tables, and summary metrics to `Results/` — `run_one_month` writes the monthly CSV, `ncd_*.npy`, and `Pcleared_*.npy` (plus the optional `dsfunction_*.xlsx` enriched by `Marginal_Check`); the public single-month and full-study helpers also write `simultaneous_charge_discharge_counts_*.csv`, whose two count fields are retained compatibility placeholders and are currently zero; `run_all_months` writes the aggregated `summary_*.csv`; `Program/analyze_summary.py` builds the monthly/annual `analysis_*.xlsx` workbook from that summary.

If any hourly natural-gas observation is missing, the model fills that gas value
with zero and records the date as a skipped gas date. To preserve calendar
alignment, the historical battery series is left at zero for that entire date;
its derived charge/discharge limits are therefore also zero, disabling
controllable battery operation on that date.

The charge/discharge limits used by each rolling optimization are selected from
the calendar day of its binding interval and then held fixed across that
optimization's 24-hour look-ahead horizon. The limits are selected again as the
binding interval advances, so they switch to zero only when the binding interval
itself enters a skipped or otherwise invalid day, and return to the monthly
limits when the binding interval enters the next valid day.

Natural-gas costs are evaluated consistently with the selected cost mode through `Program/cost_calculation.py`. The default `exact` mode constructs the cost and marginal-price functions from monthly plant-level merit-order workbooks.

## Inputs and outputs

### Principal input data

| Input | Location | Resolution/use |
|---|---|---|
| CAISO market price | `data/price/` | Five-minute price series used to form rolling forecasts |
| Historical battery output | `data/battery_data/` | Daily five-minute fleet-operation files |
| Renewable curtailment | `data/curtailment/` | Hourly curtailed renewable energy |
| Natural-gas generation | `data/ng_data/` | Hourly historical gas generation |
| Natural-gas cost stack | `data/ng_cost/` | Monthly plant-level merit-order inputs |
| Price-error scenarios | `data/random_data/` | Fixed standard-normal price-error innovations (`price_error_z_*.npy` + `manifest.json`) used by the model's forecast-error sampling |
| Historical CAISO emissions | `data/CAISO-historical-co2-20260720.csv` | Retained source data for independent historical-emissions analysis |

Each stored `price_error_z_YYYYMM.npy` array has 24 columns per binding interval. Column 0 is an unused placeholder for the exactly observed binding-interval price; columns 1--23 supply the standard-normal innovations for forecast horizons 2--24. Reproduction must therefore draw all 24 columns per binding interval and discard column 0 during simulation; generating only 23 values per interval would shift the seeded stream and produce different scenarios.

Replacement monthly price files must include the additional rolling horizon required at the end of the month: `biddingNEW` reads `price[t:t+N_t]`, so the last in-month hour needs 23 subsequent hourly prices. The committed price files contain one full extra day beyond each month (288 five-minute observations, i.e. 24 hourly values), which covers that horizon.

### Model outputs

| Type | Example | Contents |
|---|---|---|
| Monthly CSV | `January2023_eta95%_std2_exact_V5_k20.csv` | Hourly prices, profits, battery outputs, natural-gas outputs, and costs |
| NCD array | `ncd_January2023_exact_V5_k20.npy` | NCD and state-of-charge status by interval and candidate price |
| Cleared-output array | `Pcleared_January2023_exact_V5_k20.npy` | Combined controllable and passive battery output |
| Summary CSV | `summary_202301_202512_exact_V5_k20.csv` | Monthly profit, cost, gas, curtailment, and carbon metrics |
| Compatibility count CSV | `simultaneous_charge_discharge_counts_202301_202512_exact_V5_k20.csv` | Legacy `Initial_Value` and `following_value` fields; current implementation writes zero placeholders |
| Demand-supply functions | `dsfunction_May2025_exact_V5_k20.xlsx` | Optional hourly stair-function export from the single-month helpers |
| Analysis workbook | `analysis_202301_202512.xlsx` | Monthly and annual analysis sheets |

Repeated runs overwrite files with identical parameter-derived names.

## Figure source data and reproduction

`Figure_Plot/` contains the source data, plotting or numerical scripts, and committed outputs associated with the main and supplementary figures. In line with research-data reporting practice, the table below identifies the source-data location and the output that the current code produces.

| Figure | Directory | Source data | Current reproducible output |
|---|---|---|---|
| Figure 1 | `Figure_Plot/figure_1/` | `settings_sequence.xlsx` | `journalcasestudyFig1.py` solves the six stair examples with the ideal-battery (M1-equivalent, eta=1, no binary modes) formulation, computes `Res_pos` and `ResE_pos`, and writes the Power and SOC arrays back to `settings_sequence.xlsx`; it does not export a rendered figure file |
| Supplementary Figure S1 | `Figure_Plot/figure_plot_S1/` | `settings_sequence.xlsx` | `journalcasestudyFigS1_binary.py` solves the six stair examples with the two-status (binary mutual-exclusion, eta=0.9) M2 formulation, computes `Res_neg` and `ResE_neg`, and writes the Power and SOC arrays back to `settings_sequence.xlsx`; it does not export a rendered figure file |
| Figures 2 and 3 | `Figure_Plot/figure_plot_2_3/` | `settings.xlsx`, `tep2023.npy`, and `lmp2023.npy` | Generates the individual Figure 2 and Figure 3 PNG panels in `Figs/` and the seeded EV-fleet table `ev_fleet_seed42.csv`; final composite assembly is not scripted |
| Figure 4a–b | `Figure_Plot/figure_plot_4_a_b/` | Classified May 2025 demand-supply workbook in `source_data/` | Exports the state-distribution source CSV and composite PNG, SVG, and PDF |
| Figure 4c | `Figure_Plot/figure_plot_4_c/` | May 2025 classified demand-supply workbook and CAISO price CSV in `input/` | Exports an auditable source CSV and PNG, SVG, and PDF; see the directory's `README.md` |
| Figure 4d | `Figure_Plot/figure_plot_4_d/` | `analysis_202301_202512.xlsx` | Reproduces the standalone profit, carbon-reduction, and gas-cost-reduction radial plots plus the combined three-panel figure in `outputs/` |
| Figure 4e | `Figure_Plot/figure_plot_4_e/` | April 2025 model result, curtailment, and CAISO price CSVs in `input/` | Exports `Fig_4_e` as PNG, SVG, and PDF |

The copies stored inside each figure directory are the figure source-data snapshots. They are not automatically updated when files in `data/` or `Results/` are regenerated.

### Figures 1 and S1

```powershell
Push-Location Figure_Plot/figure_1
python journalcasestudyFig1.py
Pop-Location

Push-Location Figure_Plot/figure_plot_S1
python journalcasestudyFigS1_binary.py
Pop-Location
```

Each script solves the six stair-example optimisations and writes the resulting Power and SOC arrays back into `settings_sequence.xlsx`. Figure 1 uses the ideal-battery (M1-equivalent) formulation with eta=1 and no binary mode variables; Supplementary Figure S1 uses the two-status M2 formulation with binary mutual exclusion and eta=0.9. Each script reads its price table by worksheet name (`positive price case` for Fig 1, `negative price case` for Fig S1). Neither script exports a rendered figure file.

### Figures 2 and 3

```powershell
Push-Location Figure_Plot/figure_plot_2_3
python journalcasestudyFig2andFig3.py
Pop-Location
```

The script writes the individual panels to `Figure_Plot/figure_plot_2_3/Figs/`.

Three auxiliary scripts in the same directory are not called by `journalcasestudyFig2andFig3.py` but support standalone use:

- `price_plot_fig_2.py` — plots the three input price series for Figure 2 as separate PNG files in `Figs/`.
- `price_plot_fig_3.py` — plots the three price-series sets used by `Fig3_a`, `Fig3_b`, and `Fig3_c`, saving one PNG per set in `Figs/`.
- `legend.py` — exports the shared seven-state color legend as a standalone transparent PNG in `Figs/`.

### Figure 4 — automated generation

`Figure_Plot/Figure_Generation.py` is the orchestration entry point for all Figure 4 panels. It resolves canonical inputs from `Results/` and `data/`, copies fresh snapshots into each figure directory, runs the classification and plotting scripts in dependency order, and writes a provenance manifest to `Figure_Plot/Figure_Generation_manifest.json`.

```powershell
# Regenerate all Figure 4 panels
python Figure_Plot/Figure_Generation.py

# Regenerate selected panels only
python Figure_Plot/Figure_Generation.py --figures 4d 4e

# Use the existing analysis workbook without rebuilding it
python Figure_Plot/Figure_Generation.py --skip-analysis
```

The individual scripts described below can still be run directly against pre-existing figure-directory inputs.

### Figure 4a–b

The committed classified workbook can be plotted directly:

```powershell
python Figure_Plot/figure_plot_4_a_b/state_distribution_pies_May2025/plot_state_distribution_pies_May2025.py
```

### Figure 4c

```powershell
Push-Location Figure_Plot/figure_plot_4_c
python export_hourly_state_counts.py
python plot_hourly_state_counts_price_May2025.py
Pop-Location
```

For its detailed data schema and validation steps, see `Figure_Plot/figure_plot_4_c/README.md`.

### Figure 4d

```powershell
python Figure_Plot/figure_plot_4_d/plot_profit_increment_radial.py
python Figure_Plot/figure_plot_4_d/plot_reduction_rates_radial.py
```

The first script creates the standalone profit plot. The second creates the combined three-panel profit/carbon/cost figure and standalone carbon-reduction and gas-cost-reduction plots. Each current output is written as PNG, SVG, PDF, and TIFF.

### Figure 4e

```powershell
python Figure_Plot/figure_plot_4_e/Fig_4_e_plot.py
```

An April 2025 date and custom PNG path may be supplied with `--date YYYY-MM-DD --output <path>`.

## Natural-gas parameter preprocessing

`Program/CAISO-API/` contains the preprocessing workflow used to derive monthly natural-gas generator parameters from EIA-923 and EIA-860/EIA-860M data. The workflow covers source extraction, monthly splitting, heat-content checks, fuel-consumption calculations, generator-capacity matching, and marginal-fuel-cost generation.

See `Program/CAISO-API/Readme_Generator_Parameter_Generation.md` for its data flow, formulas, and commands.

## Tests

The repository contains two `unittest` suites. Run them from the directories that contain the tested modules:

```powershell
Push-Location Program/CAISO-API
python -m unittest test_build_cc_grouped_stacks.py
Pop-Location

Push-Location Figure_Plot/figure_plot_4_a_b/source_data
python -m unittest test_classify_p_ess.py
Pop-Location
```

These tests cover CA/CT grouped-stack construction and demand-supply-function classification. They do not run the Gurobi optimisation or reproduce all figures.

## Data availability and attribution

The repository includes the model inputs used in the case study, the default model outputs, the analysis workbook, and figure-specific source-data snapshots. Principal external sources are:

- California Independent System Operator (CAISO) market prices, battery output, renewable curtailment, and related power-system data;
- U.S. Energy Information Administration (EIA) EIA-923 generation and fuel data; and
- EIA-860 and EIA-860M generator-capacity data.

The monthly natural-gas capacity, fuel-consumption rate, marginal fuel cost, and merit-order stacks are project-derived from these sources. Users should retain source names, dataset dates, and all notices required by the original providers. See [EIA Copyrights and Reuse](https://www.eia.gov/about/copyrights_reuse.php) and [CAISO Privacy and Terms of Use](https://www.caiso.com/privacy-terms-of-use).

The file-group rights boundary, provider acknowledgements, and known provenance limitations are documented in [`DATA_AND_THIRD_PARTY_NOTICES.md`](DATA_AND_THIRD_PARTY_NOTICES.md).

## Licence, data terms, and disclaimer

The original software code in this repository is licensed under the [MIT License](LICENSE). The licence permits use, copying, modification, distribution, sublicensing, and sale, provided that its copyright and permission notices are retained. The software is provided without warranty, as stated in the licence.

The MIT License applies only to original software code and its associated documentation. CAISO and EIA source materials are not relicensed by this repository and remain subject to their providers' terms. Gurobi and all Python dependencies are separately licensed by their respective owners. Data, derived data, results, and figures may be subject to both project copyright and the terms of their source data unless a file-specific notice states otherwise.

This repository is provided for academic research and methodological reproduction. It does not constitute electricity-market trading, system-operation, investment, legal, or compliance advice. Users are responsible for validating the model, data, assumptions, licence conditions, and fitness for their intended use.

## Citation and contributions

Software citation metadata is provided in [`CITATION.cff`](CITATION.cff). It currently identifies Shichao Wang (`SCWang01`) as the sole creator, following the public GitHub repository information. A release version, release date, Zenodo DOI, and final paper relation will be added when the corresponding software release or publication is prepared. Until then, cite the repository URL together with the commit hash used in the analysis so the referenced code state remains identifiable.

Contributions are accepted subject to the repository's MIT License unless agreed otherwise with the repository maintainer.

The remaining internal and publication-time tasks are listed in [`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md). That checklist does not authorize an upload, push, GitHub Release, or Zenodo publication.
