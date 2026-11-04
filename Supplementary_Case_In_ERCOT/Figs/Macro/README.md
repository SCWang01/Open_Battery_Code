# Macro figures

Run these commands from the release root after generating the corresponding
[Macro system results](../../Program/Macro/README.md), or use the included results.

## Figure 4d: monthly system improvement rates

```powershell
python -B Figs/Macro/figure_plot_4_d/plot_reduction_rates_radial.py
python -B Figs/Macro/figure_plot_4_d/plot_coal_reduction_rates_radial.py
python -B Figs/Macro/figure_plot_4_d/plot_thermal_reduction_rates_radial.py
```

The entries read the `Summary` sheets in `Rolling_Result_202601.xlsx` through
`Rolling_Result_202606.xlsx`. Their input directories under
`Results/Macro_Results/System_Results/` are the root (gas), `coal/`, and `thermal/`,
respectively. Actual and Rolling require matching `Basis` values. Compatibility
with older files lacking `Basis` is limited to gas.

Each entry writes one transparent 720 dpi PNG. Output paths below are relative to
`Figs/Macro/figure_plot_4_d/outputs/ERCOT/`.

| Basis | Output | Canvas | Profit scale / interval | CO2 and cost scale / interval |
| --- | --- | --- | --- | --- |
| gas | `combined_radial/Fig_4_d_202601_202606.png` | 250 x 90 mm | 0-400% / 100% | 0-1.2% / 0.3% |
| coal | `coal/combined_radial/Fig_4_d_coal_202601_202606.png` | 300 x 110 mm | 0-400% / 100% | 0-2.5% / 0.5% |
| thermal | `thermal/combined_radial/Fig_4_d_thermal_202601_202606.png` | 300 x 110 mm | 0-400% / 100% | 0-1.2% / 0.3% |

Blue, green, and orange rings show profit increment, selected-fuel CO2 reduction,
and selected-fuel cost reduction. Each ring has six clockwise monthly sectors;
bars extend inward from the outer zero baseline. Interpret each plot using its
own scale. Negative improvements or values beyond the scale raise an error.

Profit retains the 13 ESRs' monthly trading income plus month-end inventory
value, with control share 1. The three fuel bases share the same storage
schedule and therefore the same profit ring. The formulas are:

```text
Profit increment (%) = (Rolling - Actual) / Actual * 100
CO2 or cost reduction (%) = (Actual - Rolling) / Actual * 100
```

Each denominator is the corresponding basis's Actual value. Overall rates are
computed from six-month sums of the original amounts, not from the mean of
monthly percentages. The plot reads cost and emissions from the system results;
it does not recalculate carbon factors. Gas holds coal fixed, coal holds gas
fixed, and thermal uses the combined gas/coal merit stack for Actual and Rolling.
These are static cost-stack estimates with terminal-segment extrapolation; they
do not model commitment, ramp rates, or minimum stable generation.

All entries accept `--results-dir` and `--output-root`. Explicit input paths are
used directly; the script does not append a basis subdirectory. It creates
`combined_radial/` under the output root. Example:

```powershell
python -B Figs/Macro/figure_plot_4_d/plot_coal_reduction_rates_radial.py --results-dir Results/Macro_Results/System_Results/coal --output-root Figs/Macro/figure_plot_4_d/outputs/ERCOT/coal
```

`plot_profit_increment_radial.py` supplies shared loading and rendering functions;
it is required by the three entries and is not a separate export command.

## Figure 4e: aggregate operation on 7 April 2026

```powershell
python -B Figs/Macro/figure_plot_4_e/Fig_4_e_plot.py
python -B Figs/Macro/figure_plot_4_e/Fig_4_e_plot.py --date 2026-04-07 --output Figs/Macro/figure_plot_4_e/Figs/Fig_4_e_20260407.png
```

The default output is `figure_plot_4_e/Figs/Fig_4_e_20260407.png`, a transparent
720 dpi PNG on a 180 by 64 mm canvas. The green bars show the same 13 ESRs'
`P_ESS_actual - P_ESS`: positive values mean Rolling charges more or discharges
less. Purple bars show `Total Curtailment Proxy (MW)` from the monthly
`Data/Macro/Curtailment/` workbook. The orange curve uses the already averaged
hourly `Official_Price` from `Data/Macro/Price/` in USD/MWh.

The loader selects the ERCOT local date, checks 24 consecutive UTC hours, and
compares each price and curtailment value with the independent monthly sources.
It reads the gas system result for the same month. `--project-root` can redirect
all inputs. No row-number slice or historical 0.2 scale factor is used.

Dates must be in January-June 2026 and have 24 physical hours. Missing data,
daylight-saving transition days with a different length, and values outside
the fixed axes (-1000 to 5000 MW and 0 to 70 USD/MWh) raise errors. Passing
`--date` also changes the default output filename to match the selected date;
use `--output` to choose a different destination.

## Tests

```powershell
python -B -m unittest discover -s Figs/Macro/figure_plot_4_d/tests -p "test_*.py" -v
python -B -m unittest discover -s Figs/Macro/figure_plot_4_e/tests -p "test_*.py" -v
```

The tests cover basis validation, monthly/overall rates, independent paths,
scales and out-of-range failures, plot geometry, source alignment, timezone
handling, and numeric validation. The release excludes historical CAISO inputs
and undated Figure 4e exports; the current ERCOT code does not use them.
