# Prepared Input Data

The distribution includes 186 Excel workbooks: 144 Micro input files and 42 Macro input files. These are prepared analysis inputs, rather than the complete upstream acquisition archive. Workbook numbers and raw source fields are preserved.

| Location | Files | Purpose |
|---|---:|---|
| `Micro/Six_Month_Data/` | 144 `.xlsx` and one index CSV | 24 ESRs, six monthly files each, with the first day of the following month appended |
| `Macro/Price/` | 6 | Hourly Hub Average reference prices |
| `Macro/Curtailment/` | 6 | Hourly wind/solar curtailment proxies |
| `Macro/ng_data/`, `Macro/cg_data/` | 12 | Hourly system gas and coal output |
| `Macro/ng_cost/`, `Macro/cg_cost/`, `Macro/thermal_cost/` | 18 | Monthly gas, coal, and combined thermal cost/emissions stacks |

The [experiment configuration](../Program/Micro/experiment_config_1hour.json) selects 13 of the 24 resources. Keeping the larger collection does not change the configured experiment. The remaining resources have not been represented as validated members of the supplied 13-resource result set.

See [Micro schema and coverage](Micro/README.md), [Macro schemas](Macro/README.md), and [provenance and upstream limits](PROVENANCE.md). The file inventory and SHA-256 checksums for this distribution are in [RELEASE_MANIFEST.csv](../RELEASE_MANIFEST.csv).
