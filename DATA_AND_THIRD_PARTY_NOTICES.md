# Data and Third-Party Notices

This file defines the rights boundary for the mixed software-and-data contents
of this repository. It should be read together with `LICENSE` and `README.md`.

## Original software and documentation

The MIT License in `LICENSE` applies to original software code and its
associated documentation for both the CAISO and ERCOT case studies in this
repository. The ERCOT directory includes an identical copy of that licence.
The licence does not relicense third-party
software, source data, or other material owned by external providers.

## U.S. Energy Information Administration material

The repository contains EIA-923, EIA-860, and EIA-860M source material and
project-derived natural-gas parameter tables, principally under
`Program/CAISO-API/` and `data/ng_cost/`.

The U.S. Energy Information Administration states that U.S. government
publications on its website are in the public domain and may be used or
distributed, while recommending an acknowledgement that includes the source
and publication date. EIA also notes that specifically identified third-party
material and protected logos are not covered by that general permission.

- Provider: U.S. Energy Information Administration (EIA)
- Dataset families: EIA-923, EIA-860, and EIA-860M
- Copyright and reuse terms:
  <https://www.eia.gov/about/copyrights_reuse.php>
- Suggested acknowledgement: "Source: U.S. Energy Information
  Administration", followed by the applicable source publication or release
  date shown in the retained source filename or workbook.

No EIA logo is intentionally relicensed or granted for reuse by this project.

## California Independent System Operator material

CAISO market prices, battery output, renewable-curtailment data, natural-gas
generation data, historical-emissions material, and their figure-specific
snapshots appear principally under `data/`, `Figure_Plot/`, and `Results/`.

The California ISO Terms of Use state that most publicly available material may
be used provided applicable copyright, trademark, and proprietary notices
remain intact and California ISO is credited. Third-party material and material
from access-controlled sections may have additional restrictions. These terms,
not the repository's MIT License, govern the underlying CAISO material.

- Provider: California Independent System Operator Corporation (California ISO)
- Terms of Use: <https://www.caiso.com/privacy-terms-of-use>
- Required credit: identify California ISO as the source and retain any notices
  supplied with the original material.

This repository does not grant permission to use CAISO trademarks or any
third-party material beyond permission provided by the applicable rights holder.

## Electric Reliability Council of Texas material

ERCOT resource disclosures, nodal and Hub prices, generation data, and wind/solar
reports are used in the prepared inputs under `Supplementary_Case_In_ERCOT/Data/`
and in the associated results and figure sources. The recorded product
identifiers, snapshots, transformations, and upstream limitations are documented
in the [ERCOT provenance note](Supplementary_Case_In_ERCOT/Data/PROVENANCE.md).

- Provider: Electric Reliability Council of Texas (ERCOT)
- Recorded products include `np3-965-er/60d_sced_esr_data`,
  `np6-788-cd/lmp_node_zone_hub`, `NP6-785-ER`, `NP4-732-CD`, and `NP4-737-CD`.
- The provenance note links the corresponding source products and generation
  data and records the preparation of the wind/solar curtailment proxy.

ERCOT source materials remain subject to their provider's applicable terms.
Retain source names, product identifiers, snapshot dates, and any notices supplied
with the original materials. Neither this notice nor the MIT licence grants
additional rights to redistribute ERCOT or other third-party material. The
derived curtailment proxy and fuel-cost stacks must not be described as direct
observations of system curtailment or hourly system costs.

## Project-generated data, results, and figures

`data/random_data/`, `Results/`, and the derived tables and rendered assets in
`Figure_Plot/`, together with the prepared and derived data, results, and figures
under `Supplementary_Case_In_ERCOT/Data/`, `Supplementary_Case_In_ERCOT/Results/`,
and `Supplementary_Case_In_ERCOT/Figs/`, include original project outputs as well
as transformations of the source material described above. Unless a file-specific
notice states otherwise:

- they are not covered by the MIT License merely because they are stored in the
  same repository;
- no separate CC0 or Creative Commons licence has been declared for them; and
- their reuse must preserve the applicable EIA, California ISO, and ERCOT attributions and comply
  with any applicable source-data terms.

For the Zenodo software record, retain this rights statement in the archive and
state in the record description that MIT covers original software and associated
documentation only. Do not describe the entire mixed software-and-data archive
as MIT-licensed. No additional data licence is assigned by this release; any
later licence grant for original project-generated portions must be explicitly
documented by their rights holders.

## Dependency and solver notices

Python packages, including Gurobi, are not distributed under this repository's
MIT License. Each dependency remains subject to its own licence. Running the
optimisation requires a separately obtained valid Gurobi licence.

## Provenance limitations

The repository records provider names, source workbook release identifiers,
processing scripts, and figure-input hashes where available. Exact download
URLs and retrieval dates were not preserved for every historical CAISO file.
Filename dates therefore must not be interpreted as complete acquisition
metadata. The README's documentation note applies: when descriptions and files
differ, the actual retained files and code take precedence.

The ERCOT provenance note records available product identifiers and acquisition
snapshots, but upstream downloader/exporter programs, historical resource-to-node
mapping verification, and the complete raw inputs and construction programs for
the fuel-cost stacks are not included. The distributed workflow starts from the
prepared inputs and does not establish complete raw-to-result reproducibility.
