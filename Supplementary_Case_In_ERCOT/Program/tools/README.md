# Release validation

Run from the release root with Python 3.11 or newer; no third-party packages are needed:

```powershell
python -B Program/tools/validate_release.py --skip-manifest
```

The current tree has intentional changes after the [archived packaging snapshot](../../Archive/2026-11-10/RELEASE_MANIFEST.csv) and has no current root-level inventory, so the command above skips manifest verification. Without `--skip-manifest`, the script requires `RELEASE_MANIFEST.csv` at the package root, verifies each entry against its size and SHA-256 hash, rejects unsafe paths and duplicate entries, and flags release files missing from the inventory. The manifest does not list itself. Prepare a new complete inventory before running those checks on a new publication. In either mode, the script checks:

- Python syntax without importing project modules or writing bytecode.
- JSON syntax, decoded Python/JSON strings, text files, and filenames for Chinese/Han characters.
- Local Markdown file links, including images and reference definitions.
- Workbook sheet names, cells, shared strings, and comments, including text encoded as XML entities.
- The 13 configured resources across six months: 78 source workbooks and 312 Rolling, Classified, realized-profit, and matching workbooks.
- All 78 persisted random matrices against their companion hash manifests and expected `744 x 36` float64 NPY headers.

The package's `.gitattributes` keeps text files in LF format in Git checkouts and source archives, including GitHub releases archived by Zenodo. Manifest hashes use exact file bytes. Editors must preserve LF when editing release text; binary workbooks, arrays, and figures are not converted. Before publishing a new version, regenerate the complete inventory, including intentional removals and additions, and verify it against the final package.

Standard OOXML theme font names are excluded from the language scan because they list fonts for multiple writing systems. The script does not inspect text embedded in PDF or raster images, verify external URLs or Markdown heading anchors, reproduce numerical calculations, or establish upstream data provenance.

The complete scan reads all workbook XML and may take several minutes. Failures produce specific file diagnostics and exit status 1; success returns 0. An absent release manifest is a failure with a restoration hint. `--root PATH` selects a different release copy.

After intentionally regenerating outputs, their workbook bytes and hashes will differ from the packaged snapshot. Run the structural and text checks without comparing that original snapshot:

```powershell
python -B Program/tools/validate_release.py --skip-manifest
```

Development caches, virtual environments, `.git`, runtime `reports`, and smoke/validation directories are excluded from automatic discovery. Files explicitly listed in the release manifest are always verified and scanned, including files within those normally excluded directories.
