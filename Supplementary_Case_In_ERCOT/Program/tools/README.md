# Release validation

Run from the release root with Python 3.11 or newer; no third-party packages are needed:

```powershell
python -B Program/tools/validate_release.py
```

The default command verifies each entry in `RELEASE_MANIFEST.csv` against its size and SHA-256 hash, rejects unsafe paths and duplicate entries, and flags release files missing from the inventory. The manifest does not list itself. The script also checks:

- Python syntax without importing project modules or writing bytecode.
- JSON syntax, decoded Python/JSON strings, text files, and filenames for Chinese/Han characters.
- Local Markdown file links, including images and reference definitions.
- Workbook sheet names, cells, shared strings, and comments, including text encoded as XML entities.
- The 13 configured resources across six months: 78 source workbooks and 312 Rolling, Classified, realized-profit, and matching workbooks.
- All 78 persisted random matrices against their companion hash manifests and expected `744 x 36` float64 NPY headers.

Standard OOXML theme font names are excluded from the language scan because they list fonts for multiple writing systems. The script does not inspect text embedded in PDF or raster images, verify external URLs or Markdown heading anchors, reproduce numerical calculations, or establish upstream data provenance.

The complete scan reads all workbook XML and may take several minutes. Failures produce specific file diagnostics and exit status 1; success returns 0. An absent release manifest is a failure with a restoration hint. `--root PATH` selects a different release copy.

After intentionally regenerating outputs, their workbook bytes and hashes will differ from the packaged snapshot. Run the structural and text checks without comparing that original snapshot:

```powershell
python -B Program/tools/validate_release.py --skip-manifest
```

Development caches, virtual environments, `.git`, runtime `reports`, and smoke/validation directories are excluded from automatic discovery. Files explicitly listed in the release manifest are always verified and scanned, including files within those normally excluded directories.
