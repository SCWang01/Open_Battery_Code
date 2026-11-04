# Release Preparation Notes

Prepared on November 4, 2026 (Asia/Singapore).

## Scope

This distribution is a standalone copy of the active ERCOT analysis with four main directories: `Data`, `Figs`, `Results`, and `Program`. It also contains the root README, Python requirement files, ignore rules, and the SHA-256 inventory. The original research workspace and its Git metadata were not modified by preparation.

Included data comprise all 24-resource monthly inputs, the 13-resource configured result set, and the gas/coal/thermal inputs and outputs. The active study remains January-June 2026. The additional source resources do not silently expand the modeled population.

Excluded content:

- `Writting`, manuscript source/PDFs, deliverable ZIPs, and review packets.
- Historical `Archive` trees, the old five-minute implementation, legacy June configuration, and migration/maintenance snapshots.
- `V5_Case_Study_Reference.py`, an unused older reference with unavailable dependencies.
- Obsolete CAISO and 2023-2025 plotting inputs/exports, old single-resource montages, and redundant layout variants.
- Historical local-machine logs, editor/agent settings, bytecode, caches, credentials, and `.git` metadata.

## Source changes

The numerical model and optimization objective were preserved. The release makes these operational and language changes:

1. Matching exports use the English `Notes` header; both classification figure consumers use the same name. All 78 matching workbooks were regenerated and compared against the original values after header normalization.
2. `run_pipeline.py --workers N` forwards the selected process count to validation and distributed Rolling. Its default remains 12, with one solver thread per process.
3. Missing Git on a user's system no longer prevents source-provenance collection when running a downloaded archive. Git metadata is optional.
4. The optional external legacy-cost comparison test uses `ERCOT_LEGACY_COST_MODULE` instead of a private absolute path. It skips when that external module is not provided.
5. The Figure 4e legend now says `Curtailment proxy`, matching the input definition. Its dated PNG was regenerated. April/May aggregate plotting remains available through the documented commands.
6. Current English setup, schema, algorithm, execution, and validation guides replace stale or historical instructions. Example commands name configured resources and distinguish smoke runs from production runs.
7. Classification audit files record release-relative output directories. All 14 classification figures were regenerated, and all four associated numerical CSV tables match the originals after the annotation-header translation.

Historical run-manifest and CSV path metadata are made relative to the release root. Historical source/configuration hashes retain their original meaning and are not recomputed to imply that the edited release produced the historical optimization. New `RELEASE_MANIFEST.csv` hashes identify the actual distributed files. Raw input workbooks and saved random files retain their original bytes.

## Packaging and publication

Publish this directory's contents as the repository root. Do not publish the surrounding working project, which also contains manuscript and historical material. A ZIP can contain this directory without Git metadata. Repository initialization, cloning, and Git metadata changes were deliberately left to the repository owner in their normal account.

No `LICENSE` file was invented during preparation. The owner still needs to select the code license and confirm applicable data redistribution terms. The [provenance note](../Data/PROVENANCE.md) records known data origins and explicit gaps. This distribution is prepared for that publication decision; it has not been pushed to a remote repository.

## Integrity and language coverage

`RELEASE_MANIFEST.csv` lists the relative path, byte size, and SHA-256 of each shipped file except the manifest itself. Verification checks those entries. Regenerating outputs intentionally changes their hashes.

Authored text, decoded Python/JSON strings, workbook text, SVG/XML, and PDF text were checked for Chinese. Standard OOXML theme font names may contain Chinese characters as built-in font identifiers; they are preserved to keep raw data bytes unchanged. These identifiers are not worksheet content. Raster figures were checked through matching vector text and visual inspection of standalone exports. This is an English release, not a claim that every binary metadata byte uses the ASCII alphabet.
