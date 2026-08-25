# Data and Third-Party Notices

This file defines the rights boundary for the mixed software-and-data contents
of this repository. It should be read together with `LICENSE` and `README.md`.

## Original software and documentation

The MIT License in `LICENSE` applies to original software code and its
associated documentation in this repository. It does not relicense third-party
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

## Project-generated data, results, and figures

`data/random_data/`, `Results/`, and the derived tables and rendered assets in
`Figure_Plot/` include original project outputs as well as transformations of
the source material described above. Unless a file-specific notice states
otherwise:

- they are not covered by the MIT License merely because they are stored in the
  same repository;
- no separate CC0 or Creative Commons licence has been declared for them; and
- their reuse must preserve the EIA and California ISO attributions and comply
  with any applicable source-data terms.

Before a Zenodo record is published, the depositor must either retain this as a
custom rights statement or deliberately select and document an additional data
licence for the original project-generated portions. The latter is a rights
holder decision and is not inferred by this repository.

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
