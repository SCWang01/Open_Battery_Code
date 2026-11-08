# Figures

This directory contains the plotting source, regression tests, and current ERCOT
figure exports. All scripts read the sibling `Data/`, `Results/`, and `Program/`
directories. Run the commands below from the **ERCOT release root**, the directory
containing `Data`, `Program`, `Results`, and `Figs`. No manuscript files are required.

Install the release requirements first. Plotting uses NumPy, pandas, openpyxl,
Matplotlib, SciPy, and pytz. The scripts use Matplotlib's noninteractive `Agg`
backend, so no display server is needed. Times New Roman and Arial are preferred;
Matplotlib falls back to installed fonts if either is unavailable. Data values
are unchanged by a font substitution, but text metrics and image bytes may differ.

## Reproduce the current figures

The included results support these commands without running the optimization.
Each command replaces the corresponding figure files at its default output path.

```powershell
python -B Figs/Micro/plot_classification_all.py
python -B Figs/Micro/Profit_Increment.py
python -B Figs/Micro/Classification_Test/plot_classification_example.py
python -B Figs/Micro/plot_esr_abc.py --month 202605
python -B Figs/Micro/plot_esr_abc.py --month 202604
python -B Figs/Macro/figure_plot_4_d/plot_reduction_rates_radial.py
python -B Figs/Macro/figure_plot_4_d/plot_coal_reduction_rates_radial.py
python -B Figs/Macro/figure_plot_4_d/plot_thermal_reduction_rates_radial.py
python -B Figs/Macro/figure_plot_4_e/Fig_4_e_plot.py
```

| Figure family | Inputs | Details |
| --- | --- | --- |
| Classification and telemetry comparison | `Results/Micro_Results/Classified/` and `realized_match/` | [Micro guide](Micro/README.md) |
| Monthly profit comparisons | `Results/Micro_Results/Comparison_Month.xlsx` | [Micro guide](Micro/README.md) |
| Pooled a+b and mean-ESR c | Classified results and monthly Hub prices | [Micro guide](Micro/README.md), [Figure c](Micro/figure_plot_4_c/README.md) |
| System improvement rates, Figure 4d | Gas, coal, and thermal system result summaries | [Macro guide](Macro/README.md) |
| Aggregate charging shift, Figure 4e | System hourly results, curtailment proxy, and Hub prices | [Macro guide](Macro/README.md) |

Run the Micro workflow before these commands if results have been removed or the
experiment configuration has changed. Run all three Macro basis modes before
reproducing Figure 4d from newly computed Micro results. Figure 4e uses the gas
system hourly results. See [Program](../Program/README.md) for the computation
order. Figure 4e lives in `Figs/Macro/figure_plot_4_e/`.

## Refresh after a completed Rolling run

Old classified and system workbooks are snapshots, not live views of the new
dispatch. Refresh the dependent tables before plotting. The following commands
use completed Rolling results and do not run optimization; existing realized
profit files may be reused when their source telemetry has not changed.

```powershell
python -B Program/Micro/classify_p_ess.py
python -B Program/Micro/Realized_Match.py
python -B Program/Micro/Compare_Month.py
python -B Program/Micro/Bridge.py --output-dir Results/Macro_Results/ESR_Output_Rolling
python -B Program/Macro/System_Rolling_Result.py --basis gas
python -B Program/Macro/System_Rolling_Result.py --basis coal
python -B Program/Macro/System_Rolling_Result.py --basis thermal
```

Then run the figure commands above. To export a+b/c for every configured month:

```powershell
foreach ($month in '202601','202602','202603','202604','202605','202606') {
    python -B Figs/Micro/plot_esr_abc.py --month $month
    if ($LASTEXITCODE -ne 0) { throw "Figure generation failed for $month" }
}
```

The optional `Basis_Comparison_202601_202606.csv` is a separate summary snapshot;
it is not an input to these plots and is not refreshed by the system CLI.

## Regression tests

The suites check category boundaries, exclusions, aggregation denominators, Hub
price confidence intervals, system-result mapping, and plot geometry. Some tests
read the included workbooks and some render small figures in temporary folders.

```powershell
python -B -m unittest discover -s Figs/Micro/Classification_Test -p "test_*.py" -v
python -B -m unittest discover -s Figs/Macro/figure_plot_4_d/tests -p "test_*.py" -v
python -B -m unittest discover -s Figs/Macro/figure_plot_4_e/tests -p "test_*.py" -v
```

## Release selection

The release includes the current 13-resource January-June 2026 classification
and profit figures, January-June aggregate a+b/c figures, the three canonical 4d
exports, and the dated ERCOT 4e export. It excludes historical archives, old
2023-2025 and CAISO assets, unused local input copies, single-resource a+b/c
montages, redundant layout revisions, caches, and editor/agent/repository
metadata. `assemble_esr_abc.py` is a legacy single-resource montage utility and
is not part of this release. All dependencies of the retained plotting commands
are included in the four release directories or declared Python requirements.
