# Supplied Results

The release includes historical experiment outputs so post-processing and plotting can be inspected before a new optimization run.

| Location | Contents |
|---|---|
| `Micro_Results/rolling/` | 78 resource-month dispatch workbooks, monthly CSV summaries, and six historical run manifests |
| `Micro_Results/Classified/` | 78 classified dispatch workbooks |
| `Micro_Results/realized_profit/` | 78 historical operation/revenue workbooks |
| `Micro_Results/realized_match/` | 78 comparisons of observed operation with the modeled bidding/operating categories |
| `Micro_Results/random/` | 78 fixed random innovation matrices and 78 SHA-256 metadata files |
| `Micro_Results/Comparison_Month.xlsx` | Monthly resource revenue comparison |
| `Micro_Results/figure_data/` | Derived plotting tables retained from the current analysis |
| `Macro_Results/` | Aggregated ESR output and six-month gas, coal, and thermal scenario results |

See [Macro result details](Macro_Results/README.md) and [Micro output fields](../Program/Micro/docs/ESS_Data_Processing_1hour_EN.md).

`Notes` is the English annotation column in the matching workbooks. Its values are machine-readable English diagnostic codes. Blank normalized distances can be intentional, for example for a zero power range; consult the accompanying diagnostic code rather than replacing blanks with zero.

The release preserves the numerical experiment outputs. The matching files were regenerated after translating their annotation header, with their values compared against the originals. Historical manifest and CSV path fields have been normalized to release-relative paths. Original source hashes in historical manifests continue to describe the historical source, not the edited release. Saved random arrays and their manifests remain byte-identical.

New runs overwrite the corresponding output families. Use a separate results directory for short optimization checks, as shown in the [root README](../README.md). Verification and execution create `reports/` directories as needed; old pipeline logs, local-machine audit records, and research review packets are excluded from the release.
