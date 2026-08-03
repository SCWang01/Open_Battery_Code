# CAISO Battery Energy Storage Bidding Case Study 

This repository contains a battery energy storage case study for the California Independent System Operator (CAISO) market. It uses CAISO electricity prices, battery output, renewable curtailment, and natural-gas generation data to compare historical battery operation with an aggregated optimal-bidding method. The project evaluates battery profit, natural-gas generation cost, and carbon-emission reductions, and includes scripts for result aggregation and publication figures.

> **License status:** this repository does not currently contain a `LICENSE` file. The present release is source-available but is not open-source software under an OSI-approved license. Except where permitted by law or separately authorized in writing by the copyright holder, no permission is granted to copy, modify, redistribute, sublicense, or create derivative works. See [Copyright and permissions](#copyright-and-permissions).

## Method overview

V5 divides the aggregated CAISO battery fleet into two components:

- a controllable fraction `k`, optimized through a demand-supply stair bidding function; and
- a passive fraction `1-k`, which retains its historical output trajectory.

The combined method output is:

```text
P_method = P_optimized(k-unit) + (1-k) × P_actual
```

This output is compared with the historical operation of the complete battery fleet. The default value is `k = 0.2`. When `k = 1`, the entire fleet participates in the optimization.

The default case-study settings are:

| Parameter | Default | Description |
|---|---:|---|
| Study period | 2023-01 to 2025-12 | Monthly case-study range |
| `N_t` | 24 | Number of time intervals per day |
| `eta` | 0.95 | Charge/discharge efficiency |
| `k` | 0.2 | Fraction of the fleet participating in optimal bidding |
| `N_price` | 100 | Number of candidate price points per interval |
| `meanstd` | 2 | Price-forecast error parameter |
| `COST_MODE` | `exact` | Piecewise merit-order natural-gas cost model |
| Random seed | 42 | Seed used for reproducible price perturbations |

## Repository structure

```text
Release/
├── Program/
│   ├── V5_Case_Study.py        # Main V5 case-study calculation
│   ├── cost_calculation.py     # Natural-gas cost and marginal-price models
│   ├── analyze_summary.py      # Monthly and annual Excel summary generator
│   └── CAISO-API/              # EIA/CAISO gas-unit parameter pipeline
├── data/
│   ├── battery_data/           # Historical CAISO battery output
│   ├── curtailment/            # Renewable-energy curtailment
│   ├── ng_cost/                # Monthly natural-gas merit-order stacks
│   ├── ng_data/                # Hourly natural-gas generation
│   └── price/                  # CAISO market prices
├── Figure_Plot/                # Figure scripts, source data, and exports
├── Results/                    # Unified model and analysis output directory
└── README.md
```

All core paths are resolved relative to the source files rather than the current working directory. CSV, NPY, and XLSX outputs from V5, together with the default workbook produced by `analyze_summary.py`, are written to the project-level `Results/` directory.

## Main files

### `Program/V5_Case_Study.py`

The main simulation script. It:

- reads monthly price, battery, curtailment, and natural-gas data;
- reconstructs the equivalent battery capacity and initial state of charge;
- builds and clears demand-supply stair functions with Gurobi;
- combines the optimized and passive battery components;
- calculates battery profit, natural-gas cost, renewable-energy absorption, and carbon reduction; and
- exports hourly and monthly results to `Results/`.

Running the file directly executes the complete January 2023 to December 2025 study.

### `Program/cost_calculation.py`

Provides the natural-gas generation cost functions used by V5. The default `exact` mode constructs a monthly piecewise merit-order stack from the plant-level workbooks in `data/ng_cost/`. It also provides a corresponding marginal-price calculation. A quadratic compatibility mode is retained for cases with the required fitted coefficient file.

### `Program/analyze_summary.py`

Reads the V5 monthly summary CSV, validates the study period and carbon-emission inputs, and creates an Excel workbook with:

- `Monthly Analysis`;
- `Annual Summary`; and
- `Monthly Carbon Emission`.

Unless `--output` is supplied, the workbook is written to `Results/`.

### `Program/CAISO-API/`

Contains the preprocessing pipeline used to derive monthly natural-gas unit parameters from EIA-923 and EIA-860/EIA-860M data. The pipeline covers source extraction, monthly splitting, invalid heat-content removal, fuel-consumption calculations, generator-capacity matching, and final marginal-fuel-cost generation.

See `Program/CAISO-API/Readme_Generator_Parameter_Generation.md` for the detailed pipeline and formulas.

### `Figure_Plot/`

Contains the scripts and supporting workbooks used to reproduce the study figures. Individual figure directories may contain their own source data, exported PNG/SVG/PDF/TIFF files, and local instructions.

### `data/`

Contains source and derived datasets consumed by the model. Data in this directory is not automatically covered by any future software license applied to the project code. Each dataset remains subject to its original source terms and attribution requirements.

### `Results/`

Contains model outputs and generated analysis workbooks. Re-running the model can overwrite files with the same parameter-derived names, so preserve any result set that must remain unchanged.

## Environment requirements

Python 3.10 or later is recommended. The core dependencies are:

```text
gurobipy
numpy
pandas
openpyxl
tqdm
```

The figure scripts may additionally require:

```text
matplotlib
scipy
```

Install the open-source Python dependencies with:

```powershell
python -m pip install numpy pandas openpyxl tqdm matplotlib scipy
```

The Gurobi Optimizer and its `gurobipy` package are not part of this repository. Install the Python package and configure a valid Gurobi license before running the optimization:

```powershell
python -m pip install gurobipy
```

Academic users may be eligible for an academic license under Gurobi's terms. Commercial, deployed, or other uses require a license appropriate to the intended use.

- [Gurobi Licensing](https://www.gurobi.com/product/licensing)
- [Gurobi Academic Program](https://www.gurobi.com/academics)

## Running the project

The following commands assume that the current directory is the `Release` project root.

### 1. Run the complete case study

```powershell
python Program/V5_Case_Study.py
```

This command runs every month from January 2023 through December 2025. The model repeatedly invokes Gurobi for many hourly intervals and candidate price points, so a complete run may take a substantial amount of time.

### 2. Run only May 2025

```powershell
Set-Location Program
python -c "from V5_Case_Study import run_may_2025; print(run_may_2025())"
Set-Location ..
```

This entry point also exports the hourly demand-supply function workbook for May 2025.

### 3. Generate the monthly and annual analysis workbook

After the complete case study has finished, run:

```powershell
python Program/analyze_summary.py
```

The default input is:

```text
Results/summary_202301_202512_exact_V5_k20.csv
```

The default output is:

```text
Results/analysis_202301_202512.xlsx
```

Custom input, output, and carbon-emission files can be supplied explicitly:

```powershell
python Program/analyze_summary.py Results/summary.csv `
  --output Results/analysis_custom.xlsx `
  --carbon-input path/to/CAISO-historical-co2.csv
```

## Outputs

`V5_Case_Study.py` creates the following output types in `Results/`:

| Type | Example | Contents |
|---|---|---|
| Monthly CSV | `January2023_eta95%_std2_exact_V5_k20.csv` | Hourly prices, profits, battery output, natural-gas output, and cost |
| NCD array | `ncd_January2023_exact_V5_k20.npy` | NCD and state-of-charge status for each interval and price point |
| Cleared-output array | `Pcleared_January2023_exact_V5_k20.npy` | Optimized battery cleared output |
| Summary CSV | `summary_202301_202512_exact_V5_k20.csv` | Monthly profit, cost, gas, and carbon-reduction metrics |
| Demand-supply functions | `dsfunction_May2025_exact_V5_k20.xlsx` | Optional hourly stair-function export |

Repeated runs overwrite output files with identical names. Back up required results before changing `k`, efficiency, the study period, or the cost mode.

## Data sources and attribution

The existing project documentation identifies the following principal sources:

- CAISO market prices, battery output, renewable curtailment, and related power-system data;
- U.S. Energy Information Administration (EIA) EIA-923 generation and fuel data; and
- EIA-860 and EIA-860M generator-capacity data.

The monthly natural-gas capacity, fuel-consumption rate, marginal fuel cost, and merit-order stacks are project-derived data based on those sources.

When using or redistributing source data, retain the source name, dataset date, and any required proprietary notices. Suggested attribution formats are:

```text
Source: U.S. Energy Information Administration (EIA), [dataset and date].
Source: California Independent System Operator (CAISO), [dataset and date].
```

EIA states that U.S. government publications are generally in the public domain and may be used and distributed, while recommending source and publication-date acknowledgment. This general policy does not cover the EIA logo, protected third-party photographs, or other separately protected material.

CAISO materials, data, and APIs must be used under the current CAISO website and API terms, including applicable attribution, proprietary-notice, ownership, and access provisions. Cleaning, matching, aggregating, or deriving results from source data does not cancel the terms that apply to the original source.

- [EIA Copyrights and Reuse](https://www.eia.gov/about/copyrights_reuse.php)
- [CAISO Privacy and Terms of Use](https://www.caiso.com/privacy-terms-of-use)

## Copyright and permissions

### Current project license status

No software license file is included in the repository root as of this release.

| Material | Current status | Permission boundary |
|---|---|---|
| Original project Python code and documentation | **All rights reserved; no open-source license granted** | Copying, modifying, redistributing, selling, sublicensing, or creating derivative works requires written permission from the copyright holder unless otherwise permitted by law |
| Original or directly reproduced EIA material | Subject to EIA policy | U.S. government publications are generally public domain; cite the source and date; protected third-party content and marks are excluded |
| CAISO data, materials, and APIs | Subject to current CAISO terms | Not relicensed by this project; users must independently comply with CAISO attribution, ownership, and API conditions |
| Gurobi Optimizer and `gurobipy` | Separately licensed by Gurobi | Not licensed through this project; users must obtain a valid license appropriate to their use |
| NumPy, pandas, openpyxl, and other dependencies | Subject to their own licenses | This project does not modify or replace third-party software licenses |
| Derived results in `Results/` and `Figure_Plot/` | No separate permission currently granted | May involve both project copyright and source-data terms; obtain permission and preserve attribution before public or commercial reuse |

Source visibility does not itself grant an open-source license. GitHub's licensing guidance explains that, without a license, default copyright law applies and other users do not automatically receive permission to reproduce, distribute, or create derivative works:

- [GitHub Docs — Licensing a repository](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository)

### Requirements before an open-source release

If the copyright holders decide to release the project as open-source software, they should first:

1. confirm ownership of all code, documentation, and figures, including authorization from co-authors or institutions;
2. select a license appropriate to the intended permissions, such as MIT, BSD-3-Clause, Apache-2.0, or GPL-3.0;
3. add the complete license text as a root-level `LICENSE` file and identify it in this section;
4. list third-party and large derived datasets separately rather than applying the software license to CAISO, EIA, or other external materials;
5. add a `NOTICE` or data-inventory file recording the source, date, and applicable terms for external data, code, and images; and
6. verify that no Gurobi license keys, restricted binaries, credentials, or other non-redistributable materials are included.

Until these steps are completed, the project should not be described as licensed under MIT, GPL, Apache, or any other open-source license.

## Disclaimer

This project is intended for academic research and methodological reproduction. It does not constitute electricity-market trading, system-operation, investment, legal, or compliance advice. The models, data, and results are provided in their current state. Users are responsible for validating data quality, model assumptions, license conditions, and fitness for their intended purpose.

CAISO, EIA, Gurobi, and other third-party names and trademarks belong to their respective owners. The presence of those names does not imply endorsement, warranty, sponsorship, or affiliation.

## Citation and contributions

The repository does not currently include a `CITATION.cff` file or finalized paper citation. Add a standard citation file, version number, DOI, and archival link when the associated paper or software release becomes available.

Until an open-source license is formally adopted, contact the repository maintainer before modifying, redistributing, or contributing code.
