# CAISO Battery Energy Storage Bidding Case Study

This repository provides the data, code, model outputs, and figure source files for an academic case study of aggregated battery energy storage bidding in the California Independent System Operator (CAISO) market. The study compares historical fleet operation with a bidding-based operating strategy and evaluates battery profit, natural-gas generation cost, renewable-curtailment absorption, and modelled carbon-emission reduction.

> **Licence status:** no `LICENSE` file is currently included. The repository is source-available for inspection and academic reproducibility, but no open-source licence or redistribution permission is granted. See [Licence, data terms, and disclaimer](#licence-data-terms-and-disclaimer).

## Repository contents

```text
<repo-root>/
|-- Program/
|   |-- V5_Case_Study.py        # Main V5 simulation and export workflow
|   |-- cost_calculation.py     # Natural-gas cost and marginal-price models
|   |-- analyze_summary.py      # Monthly and annual Excel analysis
|   `-- CAISO-API/              # EIA/CAISO gas-unit parameter pipeline
|-- data/
|   |-- battery_data/           # Historical CAISO battery output
|   |-- curtailment/            # Renewable-curtailment inputs
|   |-- ng_cost/                # Monthly natural-gas merit-order stacks
|   |-- ng_data/                # Hourly natural-gas generation
|   `-- price/                  # CAISO market-price inputs
|-- Results/                    # Committed model outputs and analysis workbook
|-- Figure_Plot/                # Figure scripts, source data, and rendered assets
`-- README.md                   # Reproducibility-oriented project documentation
```

The core scripts resolve their data and result paths relative to the repository rather than the shell's current directory. Some legacy figure scripts use working-directory-relative paths; their commands are provided in [Figure source data and reproduction](#figure-source-data-and-reproduction).

## Requirements

Python 3.10 or later is recommended.

### Core model and analysis

```text
gurobipy
numpy
pandas
openpyxl
tqdm
```

Install the open-source dependencies with:

```powershell
python -m pip install numpy pandas openpyxl tqdm
```

Install Gurobi's Python package separately:

```powershell
python -m pip install gurobipy
```

The optimisation requires a valid Gurobi licence. Gurobi is not distributed or licensed through this repository. See [Gurobi Licensing](https://www.gurobi.com/product/licensing) and the [Gurobi Academic Program](https://www.gurobi.com/academics).

### Figure and preprocessing dependencies

```powershell
python -m pip install matplotlib scipy xlrd scikit-learn
```

`matplotlib` and `scipy` support the figure workflows. `xlrd` is used by the gas-unit preprocessing pipeline, and `scikit-learn` is used by its standalone K-means utility.

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

### 3. Run the May 2025 case

```powershell
python Program/V5_Case_Study.py
```

Running the file directly calls `run_may_2025()`. It writes the May 2025 result files, the single-month summary, and the hourly demand-supply function workbook to `Results/`.

The equivalent import-based command is:

```powershell
Push-Location Program
python -c "from V5_Case_Study import run_may_2025; print(run_may_2025())"
Pop-Location
```

### 4. Run the complete January 2023–December 2025 study

```powershell
Push-Location Program
python -c "from V5_Case_Study import run_all_months; print(run_all_months())"
Pop-Location
```

This is the full-study entry point. It produces one set of monthly outputs for each month and writes the aggregated summary CSV to `Results/`.

## Configuration

The model currently has no command-line interface or external configuration file. Edit the module-level settings near the top of `Program/V5_Case_Study.py` before running:

```python
START_YEAR_MONTH = (2023, 1)
END_YEAR_MONTH = (2025, 12)
eta = 0.95
N_price = 300
meanstd = 2
k = 0.2
COST_MODE = "exact"
```

`k` must be in `(0, 1]`. `COST_MODE="exact"` uses the monthly piecewise merit-order data in `data/ng_cost/`; the retained `quadratic` compatibility mode requires its fitted coefficient workbook.

The `eta95%` fragment in monthly CSV filenames is currently hard-coded. Changing `eta` without also changing the naming logic can overwrite an existing file whose name still contains `eta95%`. Preserve required outputs before changing model parameters or file-naming code.

## Model workflow

`Program/V5_Case_Study.py` performs the following steps for each selected month:

1. load market price, historical battery output, renewable-curtailment, and natural-gas generation data;
2. reconstruct the equivalent fleet capacity, power limits, and initial state of charge;
3. optimise the controllable `k` fraction over rolling horizons and construct stair bidding functions;
4. combine the market-cleared controllable output with the historical passive fraction;
5. calculate historical and counterfactual battery profit, natural-gas generation and cost, renewable-curtailment absorption, and modelled carbon reduction; and
6. export hourly arrays, monthly tables, and summary metrics to `Results/`.

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
| Historical CAISO emissions | `data/CAISO-historical-co2-20260720.csv` | Retained source data for independent historical-emissions analysis |

Replacement monthly price files must include the additional 24-hour rolling horizon required at the end of the month. The committed price files contain the required additional 288 five-minute observations.

### Model outputs

| Type | Example | Contents |
|---|---|---|
| Monthly CSV | `January2023_eta95%_std2_exact_V5_k20.csv` | Hourly prices, profits, battery outputs, natural-gas outputs, and costs |
| NCD array | `ncd_January2023_exact_V5_k20.npy` | NCD and state-of-charge status by interval and candidate price |
| Cleared-output array | `Pcleared_January2023_exact_V5_k20.npy` | Combined controllable and passive battery output |
| Summary CSV | `summary_202301_202512_exact_V5_k20.csv` | Monthly profit, cost, gas, curtailment, and carbon metrics |
| Demand-supply functions | `dsfunction_May2025_exact_V5_k20.xlsx` | Optional hourly stair-function export |
| Analysis workbook | `analysis_202301_202512.xlsx` | Monthly and annual analysis sheets |

Repeated runs overwrite files with identical parameter-derived names.

## Figure source data and reproduction

`Figure_Plot/` contains the source data, plotting or numerical scripts, and committed outputs associated with the main and supplementary figures. In line with research-data reporting practice, the table below identifies the source-data location and the output that the current code produces.

| Figure | Directory | Source data | Current reproducible output |
|---|---|---|---|
| Figure 1 | `Figure_Plot/figure_1/` | `settings_sequence.xlsx` | `journalcasestudyFig1andFigS1_binary.py` computes `Res_pos` and `ResE_pos` and writes the Power and SOC arrays back to `settings_sequence.xlsx`; it does not export a rendered figure file |
| Supplementary Figure S1 | `Figure_Plot/figure_plot_S1/` | `settings_sequence.xlsx` | `journalcasestudyFig1andFigS1_binary.py` computes `Res_neg` and `ResE_neg` and writes the Power and SOC arrays back to `settings_sequence.xlsx`; it does not export a rendered figure file |
| Figures 2 and 3 | `Figure_Plot/figure_plot_2_3/` | `settings.xlsx`, `tep2023.npy`, and `lmp2023.npy` | Generates the individual Figure 2 and Figure 3 PNG panels in `Figs/`; final composite assembly is not scripted |
| Figure 4a–b | `Figure_Plot/figure_plot_4_a_b/` | Classified May 2025 demand-supply workbook in `source_data/` | Exports the state-distribution source CSV and composite PNG, SVG, and PDF |
| Figure 4c | `Figure_Plot/figure_plot_4_c/` | May 2025 classified demand-supply workbook and CAISO price CSV in `input/` | Exports an auditable source CSV and PNG, SVG, and PDF; see the directory's `README.md` |
| Figure 4d | `Figure_Plot/figure_plot_4_d/` | `analysis_202301_202512.xlsx` | Reproduces the profit plot and combined profit/carbon/cost radial figure in `outputs/` |
| Figure 4e | `Figure_Plot/figure_plot_4_e/` | April 2025 model result, curtailment, and CAISO price CSVs in `input/` | Exports `Fig_4_e` as PNG, SVG, and PDF |

The copies stored inside each figure directory are the figure source-data snapshots. They are not automatically updated when files in `data/` or `Results/` are regenerated.

### Figures 1 and S1

```powershell
Push-Location Figure_Plot/figure_1
python journalcasestudyFig1andFigS1_binary.py
Pop-Location

Push-Location Figure_Plot/figure_plot_S1
python journalcasestudyFig1andFigS1_binary.py
Pop-Location
```

Each script solves the six stair-example optimisations and writes the resulting Power and SOC arrays back into `settings_sequence.xlsx`. Neither script exports a rendered figure file.

### Figures 2 and 3

```powershell
Push-Location Figure_Plot/figure_plot_2_3
python journalcasestudyFig2andFig3.py
Pop-Location
```

The script writes the individual panels to `Figure_Plot/figure_plot_2_3/Figs/`.

Three auxiliary scripts in the same directory are not called by `journalcasestudyFig2andFig3.py` but support standalone use:

- `price_plot_fig_2.py` — plots the three input price series for Figure 2 as separate PNG files in `Figs/`.
- `price_plot_fig_3.py` — plots the three price-series sets used by `Fig3_d`, `Fig3_e`, and `Fig3_f`, saving one PNG per set in `Figs/`.
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

The second script creates the combined three-panel profit, carbon-reduction, and gas-cost-reduction figure. Some separately committed historical artifacts in the output subdirectories are not regenerated by the current scripts.

### Figure 4e

```powershell
python Figure_Plot/figure_plot_4_e/Fig_4_e_plot.py
```

An April 2025 date and custom PNG path may be supplied with `--date YYYY-MM-DD --output <path>`.

## Natural-gas parameter preprocessing

`Program/CAISO-API/` contains the preprocessing workflow used to derive monthly natural-gas generator parameters from EIA-923 and EIA-860/EIA-860M data. The workflow covers source extraction, monthly splitting, heat-content checks, fuel-consumption calculations, generator-capacity matching, and marginal-fuel-cost generation.

See `Program/CAISO-API/Readme_Generator_Parameter_Generation.md` for its data flow, formulas, and commands.

## Data availability and attribution

The repository includes the model inputs used in the case study, the default model outputs, the analysis workbook, and figure-specific source-data snapshots. Principal external sources are:

- California Independent System Operator (CAISO) market prices, battery output, renewable curtailment, and related power-system data;
- U.S. Energy Information Administration (EIA) EIA-923 generation and fuel data; and
- EIA-860 and EIA-860M generator-capacity data.

The monthly natural-gas capacity, fuel-consumption rate, marginal fuel cost, and merit-order stacks are project-derived from these sources. Users should retain source names, dataset dates, and all notices required by the original providers. See [EIA Copyrights and Reuse](https://www.eia.gov/about/copyrights_reuse.php) and [CAISO Privacy and Terms of Use](https://www.caiso.com/privacy-terms-of-use).

## Licence, data terms, and disclaimer

No root-level software licence is included. Unless separately authorised by the copyright holder or permitted by law, the original code and documentation may not be copied, modified, redistributed, sublicensed, or used to create derivative works. Source visibility alone does not grant an open-source licence.

CAISO and EIA source materials remain subject to their providers' terms and are not relicensed by this repository. Gurobi and all Python dependencies are separately licensed by their respective owners. Derived data, results, and figures may be subject to both project copyright and the terms of their source data.

This repository is provided for academic research and methodological reproduction. It does not constitute electricity-market trading, system-operation, investment, legal, or compliance advice. Users are responsible for validating the model, data, assumptions, licence conditions, and fitness for their intended use.

## Citation and contributions

The repository does not currently include a `CITATION.cff`, DOI, or final paper citation. These should be added when the associated publication or archived software release becomes available.

Until a software licence and contribution policy are formally adopted, contact the repository maintainer before modifying, redistributing, or contributing code.
