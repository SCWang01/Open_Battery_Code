# Release notes

## v1.1.0 — CAISO and ERCOT battery case studies

Prepared for publication on November 5, 2026 (Asia/Singapore). The `date-released`
field in `CITATION.cff` is set to this intended publication date; update it before
tagging if the release is published on a different date. These notes do not
assert that the GitHub release or Zenodo record has already been published.

### Changes since v1.0.0

- Add the ERCOT supplementary case for January–June 2026, with 13 configured
  Energy Storage Resources and prepared inputs covering 24 resources. Include
  resource-level optimization, historical-operation comparisons, and system
  accounting under gas, coal, and combined thermal displacement assumptions.
- Extend the CAISO workflows with independent and parallel self-scheduling
  entry points, uncertainty comparisons, classification post-processing, and
  updated monthly and annual analysis.
- Document separate CAISO and ERCOT environments, figure reproduction commands,
  supplied results, data sources, and interpretation limits.
- Align software citation metadata with the two-case scope and version `1.1.0`.
  Apply the existing MIT licence consistently to original code and associated
  documentation in both cases, while preserving separate data rights notices.
- Include an ERCOT file inventory and a standard-library validator for file
  hashes, syntax, local links, configured workbook coverage, and saved random
  matrices. Keep ERCOT text files in LF format across platforms so the inventory
  matches both the working tree and GitHub/Zenodo source archives. Updated
  publication documents and text-file checksums are reflected in that inventory.

### Environments and reproduction scope

| Case | Reference environment | Setup and reproduction |
|---|---|---|
| CAISO | CPython 3.10.17 and root `requirements.txt` | [CAISO guide](README.md#caiso-quick-start) |
| ERCOT | Python 3.12.7 and its own requirement files | [ERCOT guide](Supplementary_Case_In_ERCOT/README.md) |

Optimization requires a separately obtained Gurobi licence. The ERCOT core
environment supports documented post-processing and plotting from supplied
results without solving the optimization models. The two cases have separate
configurations, inputs, results, and figure workflows.

ERCOT upstream acquisition/export programs, historical node-mapping verification,
and complete fuel-stack construction materials are outside this distribution.
The supplied inputs support the documented prepared-data workflow. See the
[ERCOT provenance note](Supplementary_Case_In_ERCOT/Data/PROVENANCE.md) and
[data and third-party notices](DATA_AND_THIRD_PARTY_NOTICES.md). This publication
preparation does not constitute a new full optimization run.

Run the ERCOT packaging check from `Supplementary_Case_In_ERCOT/`:

```powershell
python -B Program/tools/validate_release.py
```

This check verifies package integrity and structure; it does not verify numerical
model results or establish upstream data permissions.

## GitHub and Zenodo publication

1. Verify the intended publication date in `CITATION.cff` and retain the creator
   order: Ye Guo, Chenge Gao, Shichao Wang. Keep any DOI or paper identifier absent
   until it has actually been assigned and verified.
2. Commit the release-preparation files using the repository owner's normal
   Windows account or the GitHub interface, including this file and the new
   ERCOT `.gitattributes` and `LICENSE`. If the ERCOT files change again,
   update their `RELEASE_MANIFEST.csv` entries and rerun the packaging check.
   The new release must point to the commit containing these final files.
3. Sign in to [Zenodo](https://zenodo.org/), connect GitHub, and enable
   `SCWang01/Open_Battery_Code` in the GitHub integration before creating the new
   release. Follow [Zenodo's connection guide](https://help.zenodo.org/docs/github/enable-repository/).
4. In the [GitHub releases page](https://github.com/SCWang01/Open_Battery_Code/releases),
   create a new `v1.1.0` tag at that final commit and publish a normal release with
   the title `v1.1.0 — CAISO and ERCOT battery case studies`. Use the changes and
   reproduction-scope sections above as the release description. Preserve the
   existing `v1.0.0` tag and release.
5. Wait for [Zenodo to archive the release](https://help.zenodo.org/docs/github/archive-software/github-upload/).
   Check that the record is Software, its version is `1.1.0`, and the title and
   creators match `CITATION.cff`. Check the archive contains both case studies,
   their inputs, dependency files, notices, and documentation. Retain the explicit
   rights boundary: MIT covers original code and documentation; other archive
   contents follow `DATA_AND_THIRD_PARTY_NOTICES.md` and the ERCOT provenance note.
6. Open the assigned **version DOI**, download the archive, and check that it is
   the intended release. Export its citation and use the version DOI in the
   manuscript, following [Zenodo's versioning guidance](https://zenodo.org/help/versioning).
   Add the assigned DOI to the root README and the `doi` field in `CITATION.cff`
   in a subsequent documentation commit; do not move the published release tag.
   When preparing a later software version, remove or replace the old
   version-specific DOI so it cannot be mistaken for the new version's identifier.

Zenodo can read `CITATION.cff` directly. A `.zenodo.json` file is not required;
if one is added later, its metadata takes precedence on Zenodo and must be kept
consistent with the citation file. See [Zenodo's metadata guidance](https://help.zenodo.org/docs/github/describe-software/).

### Manuscript citation after archiving

Use the following only after the record has been published and replace the
bracketed DOI with the actual version DOI:

> The code used in this study is archived on Zenodo (version 1.1.0;
> [VERSION DOI]). The development repository is available at
> https://github.com/SCWang01/Open_Battery_Code.

The software reference should identify Ye Guo, Chenge Gao, and Shichao Wang;
*CAISO and ERCOT Battery Energy Storage Case Studies*; version 1.1.0; the actual
publication year; Zenodo; and the assigned version DOI. Export BibTeX or another
required reference format from the published record rather than inventing a DOI.
Add the related paper identifier to the record when that identifier is available.
