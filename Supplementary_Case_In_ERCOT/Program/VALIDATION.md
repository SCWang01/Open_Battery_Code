# Validation Record and Current Scope

## November 10 documentation cleanup

The current cleanup checks source/configuration syntax, documentation links, command entry-point paths, and references to removed utilities. One bytecode file and its empty cache directory, plus an empty test directory, were cleared before the archive-only instruction. The duplicate next-day-data note is preserved under `Archive/2026-11-10/`. Current prepared-data guidance is consolidated in `Data/Micro/README.md` and `Data/PROVENANCE.md`. The EIA-860 reference collection remains under `Data/External/` and is explicitly included by the upload ignore rules; see `Data/External/README.md`.

Result documents, numerical outputs, and rendered figures were not reviewed or regenerated in this cleanup. Optimization, result-dependent tests, and the complete release validator were not run. The earlier `RELEASE_MANIFEST.csv` is preserved unchanged under `Archive/2026-11-10/` as a historical packaging snapshot. It does not certify the updated working tree, which has no current root-level inventory.

Source/document checks passed for 40 Python syntax parses, one experiment JSON configuration, 19 Markdown link scans, and all 16 current pipeline stage paths. The archived next-day-data note matches its original bytes; the inventory move preserved its SHA-256. Ignore-rule checks confirm that `Data/External/**` and data documentation are eligible for upload while new prepared input binaries remain ignored.

## Historical preparation checks

Validation was performed during release preparation on November 4, 2026 using Windows and Python 3.12.7. Installed package versions are recorded in the root requirement files. Those pins describe the validation environment; a fresh installation on another operating system was not tested.

### Passed checks recorded on November 4

| Check | Coverage |
|---|---|
| Existing Macro tests | 49 passed; one optional external legacy comparison skipped |
| Existing figure tests | 94 passed: 57 Micro, 28 Figure 4d, 9 Figure 4e |
| Macro input contracts | All 42 workbooks, including hourly DST grids and 18 gas/coal/thermal stacks |
| Macro numerical integration | May 2026 thermal recomputation matched all 744 rows and 38 columns of the supplied hourly table; numerical tolerance `rtol=1e-10`, `atol=1e-7` |
| Saved Rolling frame validation | All 78 resource-month workbooks, 56,459 executed hourly resource records |
| Matching regeneration | All 78 workbooks regenerated with `Notes`; values compared with originals after header translation |
| Forecast innovations | All 78 saved matrices and their metadata/hash contracts |
| Micro input preflight | BIG_STAR_ESR1, June 2026, one worker; no optimization |
| Pipeline controls | Worker forwarding, the then-current ten stage paths, and missing-Git handling checked; the current pipeline has 16 stages, including Rolling verification and six monthly a/b/c exports |
| Figure regeneration | All 14 classification figures, May aggregate a+b/c, the dated ERCOT Figure 4e, and shared input loading for all 18 Macro result workbooks |
| Classification export equality | All four CSV tables match the originals exactly after the annotation-header translation, including 56,459 telemetry records |
| Figure language/export checks | 30 PDF text extractions, 15 SVGs, 38 valid PNGs, and visual checks of eight standalone raster exports |

Python source syntax, JSON, local documentation links, release paths, workbook text, configured input/result coverage, and release checksums are also checked by `Program/tools/validate_release.py`.

## Commands to repeat the main checks

From the release root:

```bash
python -B Program/tools/validate_release.py --skip-manifest
python -B -m unittest discover -s Program/Macro/tests -v
python -B -m unittest discover -s Figs/Micro/Classification_Test -p "test_*.py" -v
python -B -m unittest discover -s Figs/Macro/figure_plot_4_d/tests -p "test_*.py" -v
python -B -m unittest discover -s Figs/Macro/figure_plot_4_e/tests -p "test_*.py" -v
python -B Program/Micro/Experiment_Data_1hour.py --experiment Program/Micro/experiment_config_1hour.json --verify-results rolling
python -B Program/Micro/Rolling_Window_1hour.py --month 2026-06 --member BIG_STAR_ESR1 --validate-only --workers 1
```

The first command is read-only but reads result workbook XML and persisted random files. It skips inventory verification because the earlier inventory is archived; generate a new complete root-level inventory before validating a new publication without `--skip-manifest`. Scientific verification/preflight entry points may write reports under the configured result root. A single-resource preflight report is not a full audit of all input resources or all six months.

## Limits

The full optimization was not rerun during the November 4 release preparation or this documentation cleanup, and solver license availability was not certified by either activity. This statement does not describe intervening experiment runs. No new optimality claim is made for the supplied outputs. A short solver execution and full run are documented in the root README for an appropriately licensed environment.

Input preparation starts from the supplied workbooks. Upstream API acquisition and fuel-stack construction were not reproduced. Tests check implementation behavior and selected numerical consistency, not the validity of every economic assumption. Linux/macOS execution, a fresh dependency installation, and exact cross-platform figure/solver byte reproducibility remain untested.
