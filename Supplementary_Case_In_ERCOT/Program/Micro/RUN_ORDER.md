# Micro execution order

Run these commands from the release root containing `Data`, `Figs`, `Results`, and `Program`. The default configuration selects 13 resources and January-June 2026. Run each dependent stage only after its inputs are complete. Same-named results are replaced.

## Complete Micro workflow

```powershell
python -u -B Program/Micro/run_pipeline.py --workers 4
```

The default worker count is 12; override it to fit hardware and solver-license concurrency. The pipeline runs preflight, distributed Rolling, realized profit, classification, matching, profit comparison, classification plots, profit plots, the single-resource example, and full-month Rolling validation. Per-stage logs and `status.json` are written under `Results/Micro_Results/reports/pipeline_runs/<timestamp>/`. The pipeline excludes the optional Benchmark, the Macro bridge/system calculations, and the combined publication panels; their commands are documented separately below or in [Figs](../../Figs/README.md).

## 1. Validate and produce Rolling and realized results

```powershell
python -u -B Program/Micro/Rolling_Window_1hour.py --workers 4 --validate-only
python -u -B Program/Micro/distributed_Rolling_Window_1hour.py --workers 4 --solver-threads 1
python -u -B Program/Micro/Reality_Profit.py
```

The first command validates inputs and writes quality reports without solving. The second requires a valid Gurobi license. The third independently aggregates telemetry and paired five-minute realized profit; it does not depend on Rolling output.

| Product | Location |
|---|---|
| Rolling dispatch | `Results/Micro_Results/rolling/<resource>_Rolling_Window_1hour_<YYYYMM>.xlsx` |
| Realized telemetry and profit | `Results/Micro_Results/realized_profit/Realized_Profit_<resource>_1hour_<YYYYMM>.xlsx` |
| Input audit | `Results/Micro_Results/reports/monthly_input_quality_1hour_<YYYYMM>.json` and `.csv` |
| Forecast innovations | `Results/Micro_Results/random/` |

Rolling and realized entry points accept `--member BIG_STAR_ESR1 --month 2026-06`. Repeat `--member` to select several configured resources. The source script is named `Reality_Profit.py` while its output prefix is `Realized_Profit_`.

## 2. Classify executed Rolling power

```powershell
python -B Program/Micro/classify_p_ess.py
```

Read monthly Rolling files and classify execution using `dsfunction` and that row's `Pcmax`/`Pdmax`. Output: `Results/Micro_Results/Classified/classified_<resource>_1hour_<YYYYMM>.xlsx`, including `P_ESS_state`. The classification CLI supports `--input` and `--output` for an individual workbook, or `--input-dir` and `--output-dir` for a collection.

## 3. Match telemetry to category power points

```powershell
python -B Program/Micro/Realized_Match.py
```

This reads Rolling and realized files directly, matching by resource, month, and `Hour Start UTC`; it does not consume Classified files. Output: `Results/Micro_Results/realized_match/Realized_Match_<resource>_1hour_<YYYYMM>.xlsx`.

`MC`, `CC`, `CD`, `NU`, `DC`, `DD`, and `MD` are signed normalized gaps. Telemetry is clipped to the Rolling effective bounds for the calculation, and `Original Telemetered Net Output` preserves the original hourly mean. Explanations appear in `Notes`.

To select one resource-month:

```powershell
python -B Program/Micro/Realized_Match.py --resource BIG_STAR_ESR1 --month 2026-06
```

## 4. Generate summaries and Micro figures

```powershell
python -B Program/Micro/Compare_Month.py
python -B Figs/Micro/plot_classification_all.py
python -B Figs/Micro/Profit_Increment.py
python -B Figs/Micro/Classification_Test/plot_classification_example.py
```

`Compare_Month.py` reads Rolling, realized, and source Usage workbooks and writes `Results/Micro_Results/Comparison_Month.xlsx`. Classification figures read Classified and Match files, write images to `Figs/Micro/Classification_All/`, and write statistics and audits to `Results/Micro_Results/figure_data/Classification_All/`. Profit figures read the comparison workbook and write to `Figs/Micro/Six_Month_Comparison_1hour/`.

The example uses BIG_STAR_ESR1 over all six months and writes beside its script. Its module also supplies shared classification and plotting helpers. For other figure families and composed panels, see the [figure guide](../../Figs/README.md). Plotting from supplied results requires no optimization run.

## 5. Validate full-month Rolling results

```powershell
python -B Program/Micro/Experiment_Data_1hour.py --experiment Program/Micro/experiment_config_1hour.json --verify-results rolling
```

The verifier requires a completed monthly run manifest and checks each listed member's complete hourly timeline, participation, physical bounds, energy continuity, and settlement. It writes monthly verification JSON under `Results/Micro_Results/reports/`. It is a physical and arithmetic consistency check, not a claim that current code hashes match the historical source used for supplied outputs.

## 6. Bridge to system-level analysis

```powershell
python -B Program/Micro/Bridge.py --output-dir Results/Macro_Results/ESR_Output_Rolling
python -B Program/Macro/System_Rolling_Result.py
```

With the explicit output directory above, `Bridge.py` aligns all 13 configured resources by `Hour Start UTC`, sums Rolling `Cleared MW`, and writes `Results/Macro_Results/ESR_Output_Rolling/ESR_Rolling_Output_<YYYYMM>.xlsx`, where Macro reads its input. Its standalone default is the older `ESR_Output/` directory, so retain the explicit argument for this workflow. It accepts `--month 2026-03`; omit the flag for all configured months. The `Output` worksheet retains local `Interval Start`, UTC hours, resource columns, and `ESR_Output_MW` (MW, positive for discharge). March has 743 physical hours. Missing members, invalid output, duplicate hours, or missing hours fail before the selected outputs are written. Rerun the bridge after changing Rolling results. See [Macro instructions](../Macro/README.md) for system inputs, calculation assumptions, and outputs.

## Optional Benchmark

```powershell
python -B Program/Micro/Benchmark_1hour.py --workers 4
python -B Program/Micro/Experiment_Data_1hour.py --experiment Program/Micro/experiment_config_1hour.json --verify-results benchmark
```

Benchmark clears historical TPO bid curves under Piecewise and Discrete rules with independent reconstructed energy trajectories. It uses telemetry-derived power ratings, unlike the submitted HSL/LSL extrema used by current Rolling. It is not executed by `run_pipeline.py`, and benchmark outputs need to be generated before their verification command can run.

## Updating an experiment

After changing Rolling power bounds, rerun Rolling, classification, matching, comparison, and their figures. Recompute Bridge and Macro outputs if the dispatch changes. Unchanged source telemetry does not imply unchanged matching: clipping, normalization, endpoints, and curve categories all depend on Rolling bounds.

Use a separate result directory for reduced `--steps` runs so incomplete months cannot replace supplied complete results. See the [Micro README](README.md#configuration-and-reproducibility) for auxiliary-output behavior and configuration isolation. `Rolling_Window_1hour.py` provides the common implementation and preflight entry point; its current CLI also delegates to the same distributed runner.
