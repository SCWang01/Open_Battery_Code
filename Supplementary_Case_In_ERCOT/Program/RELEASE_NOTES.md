# Release Preparation Notes

Initial package prepared on November 4, 2026 (Asia/Singapore). Publication documentation updated on November 5, 2026 for the planned repository release `v1.1.0`; this preparation date does not establish that the release or a Zenodo record has been published.

## November 10, 2026: documentation and residual-file cleanup

The current source/configuration uses monthly raw submitted SOC bounds, first-observation monthly initialization, and unrounded HSL/LSL extrema for Rolling. The Micro pipeline includes full-month Rolling verification and pooled a/b/c exports for every configured month. The entry point is `Program/Micro/run_pipeline.py`; machine-specific launcher and efficiency-estimation instructions have been removed because those programs are absent.

One Python bytecode file and its empty cache directory, plus the empty `Program/Micro/tests/` directory, were cleared before the archive-only instruction. No current source or figure program consumes `Data/External/EIA860/`; this reference collection and its downloaded archive remain available for upload through an explicit ignore-rule exception. See [the external-data note](../Data/External/README.md). The duplicate monthly-tail note is preserved under [Archive](../Archive/README.md), with current guidance consolidated into the Micro input guide and data provenance note. `Figs/Micro/Classification_Test/` is retained because the batch plotter imports its shared implementation and the directory contains active tests.

The cleanup reviews source and explanatory documentation only; result documents, numerical outputs, and rendered figures were not reviewed or regenerated. The original release inventory is preserved unchanged under `Archive/2026-11-10/RELEASE_MANIFEST.csv`. No current inventory is placed at the package root. Use `validate_release.py --skip-manifest` for structural checks after intentional updates; it still reads result files. Generate and verify a complete root-level inventory before publishing a new snapshot. The preparation and checksum statements below describe the earlier package, not the updated tree.

## Scope

This package is included as `Supplementary_Case_In_ERCOT/` in the CAISO and ERCOT Battery Energy Storage Case Studies repository. Its four main directories are `Data`, `Figs`, `Results`, and `Program`. It also contains its own README, Python requirement files, MIT license copy, ignore rules, and SHA-256 inventory. The ERCOT environment and execution paths remain independent of the CAISO workflow. Initial package preparation did not modify the original research workspace or its Git metadata.

Included data comprise all 24-resource monthly inputs, the 13-resource configured result set, and the gas/coal/thermal inputs and outputs. The active study remains January-June 2026. The additional source resources do not silently expand the modeled population.

Content excluded from the ERCOT package:

- `Writting`, manuscript source/PDFs, deliverable ZIPs, and review packets.
- Historical `Archive` trees, the old five-minute implementation, legacy June configuration, and migration/maintenance snapshots.
- `V5_Case_Study_Reference.py`, an unused older reference with unavailable dependencies.
- Obsolete CAISO and 2023-2025 plotting inputs/exports, old single-resource montages, and redundant layout variants.
- Historical local-machine logs, editor/agent settings, bytecode, caches, credentials, and `.git` metadata.

## Source changes

Initial package preparation preserved the numerical model and optimization objective and made these operational and language changes:

1. Matching exports use the English `Notes` header; both classification figure consumers use the same name. All 78 matching workbooks were regenerated and compared against the original values after header normalization.
2. `run_pipeline.py --workers N` forwards the selected process count to validation and distributed Rolling. Its default remains 12, with one solver thread per process.
3. Missing Git on a user's system no longer prevents source-provenance collection when running a downloaded archive. Git metadata is optional.
4. The optional external legacy-cost comparison test uses `ERCOT_LEGACY_COST_MODULE` instead of a private absolute path. It skips when that external module is not provided.
5. The Figure 4e legend now says `Curtailment proxy`, matching the input definition. Its dated PNG was regenerated. April/May aggregate plotting remains available through the documented commands.
6. Current English setup, schema, algorithm, execution, and validation guides replace stale or historical instructions. Example commands name configured resources and distinguish smoke runs from production runs.
7. Classification audit files record release-relative output directories. All 14 classification figures were regenerated, and all four associated numerical CSV tables match the originals after the annotation-header translation.

Historical run-manifest and CSV path metadata are made relative to the release root. Historical source/configuration hashes retain their original meaning and are not recomputed to imply that the edited release produced the historical optimization. New `RELEASE_MANIFEST.csv` hashes identify the actual distributed files. Raw input workbooks and saved random files retain their original bytes.

## Packaging and publication

Publish the parent CAISO/ERCOT repository as release `v1.1.0`, retaining this package under `Supplementary_Case_In_ERCOT/`. The GitHub and Zenodo source archive should contain both case studies and their documentation. A separate copy of this ERCOT directory can still be used with its own environment and documented commands. Manuscript sources, historical working-project material, and local Git metadata are outside this package. Repository initialization, cloning, and Git metadata changes remain the repository owner's responsibility in their normal account.

The original ERCOT code and associated documentation use the repository's MIT License. The [included copy](../LICENSE) preserves its copyright and permission notices for users working with this package separately. Third-party data and software retain their applicable terms; the MIT License does not apply to data, derived data, results, or figures merely because they accompany the code. No separate CC0 or Creative Commons license is assigned to those materials here. The [Micro input guide](../Data/Micro/README.md) and [Macro input guide](../Data/Macro/README.md) describe prepared inputs and upstream reproducibility limits, without asserting third-party redistribution permission.

The November 5 publication updates align the license and packaging descriptions, normalize text line endings to LF, and refresh the affected file checksums in `RELEASE_MANIFEST.csv`. The package's `.gitattributes` preserves LF in cross-platform Git checkouts so the manifest also verifies GitHub and Zenodo source archives. The normalization restores the text bytes already stored in Git; it does not change numerical values, binary data, or historical source/configuration provenance hash fields. The edited publication documents have their own updated release checksums.

## Integrity and language coverage

The historical `RELEASE_MANIFEST.csv` lists the relative path, byte size, and SHA-256 of each file in that packaging snapshot except the manifest itself. Verification checks those entries. Subsequent source edits, removals, additions, and regenerated outputs no longer match that snapshot.

Authored text, decoded Python/JSON strings, workbook text, SVG/XML, and PDF text were checked for Chinese. Standard OOXML theme font names may contain Chinese characters as built-in font identifiers; they are preserved to keep raw data bytes unchanged. These identifiers are not worksheet content. Raster figures were checked through matching vector text and visual inspection of standalone exports. This is an English release, not a claim that every binary metadata byte uses the ASCII alphabet.
