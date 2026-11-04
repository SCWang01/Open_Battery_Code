# Programs

| Location | Responsibility |
|---|---|
| [Micro](Micro/README.md) | Hourly resource data loading, optimization, realized revenue, classification, matching, and comparison |
| [Macro](Macro/README.md) | Aggregate the same resources and estimate system generation, curtailment proxy, cost, and emissions changes |
| [tools/validate_release.py](tools/validate_release.py) | Validate packaged files and the SHA-256 inventory using the Python standard library |

Read the [root setup and commands](../README.md), then [Micro run order](Micro/RUN_ORDER.md) or [Macro instructions](Macro/README.md). Plotting programs are kept beside their outputs in [Figs](../Figs/README.md).

The code is run as scripts, not as an installed Python package. Script imports intentionally resolve sibling modules. Do not add all nested directories indiscriminately to `PYTHONPATH`.

Existing regression tests can be run from the release root:

```bash
python -B -m pytest Program/Macro/tests Figs/Micro/Classification_Test Figs/Macro/figure_plot_4_d/tests Figs/Macro/figure_plot_4_e/tests -q -p no:cacheprovider
```

See [validation notes](VALIDATION.md) and [release changes](RELEASE_NOTES.md).
