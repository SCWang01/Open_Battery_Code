# External reference data

`EIA860/` contains a resource crosswalk, the downloaded 2025 EIA-860 archive, extracted workbooks, instructions, and download provenance from an earlier diagnostic workflow. No current program under `Program/` or `Figs/` references this directory. Rolling reads the monthly ERCOT Usage workbooks and obtains its SOC and power bounds from their submitted fields.

This collection is retained for upload alongside the case study, including the downloaded archive, extracted source files, crosswalk, and provenance. Repository ignore rules explicitly include `Data/External/**`. It is outside the 186 active prepared input workbooks and is not required by the documented Micro, Macro, or plotting commands. The November 10 documentation cleanup did not inspect the workbook or PDF contents.
