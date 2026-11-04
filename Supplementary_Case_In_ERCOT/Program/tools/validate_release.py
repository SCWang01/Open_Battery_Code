"""Validate release integrity, English text, links, and configured file coverage.

This standard-library check reads files without importing project modules or
creating bytecode. It checks the shipped snapshot by default; use --skip-manifest
after intentionally regenerating results. It does not solve or validate the
numerical models, inspect text in PDF/raster figures, or prove data provenance.
"""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import html
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import struct
import sys
import time
from urllib.parse import unquote, urlsplit
from zipfile import BadZipFile, ZipFile


DEFAULT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_NAME = "RELEASE_MANIFEST.csv"
TEXT_SUFFIXES = {
    ".py", ".md", ".txt", ".json", ".csv", ".tsv", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".ps1", ".sh", ".xml", ".svg", ".html",
    ".css", ".js", ".rst",
}
IGNORED_DIRECTORIES = {
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", "node_modules", "reports", "smoke", "validation",
}
HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U000323af]")
SHA256 = re.compile(r"[0-9a-fA-F]{64}")
MONTH = re.compile(r"\d{4}-(?:0[1-9]|1[0-2])")
INLINE_LINK = re.compile(r"!?\[[^\]\n]*\]\(\s*(?:<([^>\n]+)>|([^\s)]+))(?:\s+['\"][^\n]*?['\"])?\s*\)")
REFERENCE_LINK = re.compile(r"^\s*\[[^\]\n]+\]:\s*(?:<([^>\n]+)>|(\S+))", re.MULTILINE)


class Audit:
    """Collect independent failures so one invocation reports all repair targets."""

    def __init__(self, root: Path):
        self.root = root
        self.errors: list[str] = []
        self.counts: dict[str, int] = {}

    def error(self, message: str) -> None:
        self.errors.append(message)

    def label(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    def count(self, category: str) -> None:
        self.counts[category] = self.counts.get(category, 0) + 1

    def check_english(self, text: str, location: str) -> None:
        if HAN.search(text):
            self.error(f"Chinese/Han text found: {location}")


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def safe_manifest_path(root: Path, value: str) -> Path:
    """Accept only forward-slash relative file paths that resolve inside root."""
    logical = PurePosixPath(value)
    if (
        not value or "\\" in value or "\x00" in value
        or logical.is_absolute() or PureWindowsPath(value).drive
        or ".." in logical.parts or value in {".", ""}
    ):
        raise ValueError(f"unsafe manifest path: {value!r}")
    path = root.joinpath(*logical.parts)
    if not path.resolve().is_relative_to(root):
        raise ValueError(f"manifest path resolves outside release: {value!r}")
    return path


def inventory(root: Path) -> set[Path]:
    """Exclude local development/runtime trees; never follow directory symlinks."""
    files: set[Path] = set()
    for folder, directories, names in os.walk(root, followlinks=False):
        directories[:] = [
            name for name in directories
            if name.lower() not in IGNORED_DIRECTORIES
            and "smoke" not in name.lower()
            and not name.lower().startswith("validation")
        ]
        for name in names:
            path = Path(folder) / name
            if path.suffix.lower() in {".pyc", ".pyo"}:
                continue
            files.add(path)
    return files


def verify_manifest(audit: Audit, files: set[Path]) -> set[Path]:
    """Check every manifest row, including rows inside otherwise ignored trees."""
    manifest = audit.root / MANIFEST_NAME
    if not manifest.is_file():
        audit.error(f"Missing {MANIFEST_NAME}; restore the release manifest or use --skip-manifest for regenerated outputs")
        return set()
    listed: set[Path] = set()
    identities: set[str] = set()
    try:
        with manifest.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not {"path", "size_bytes", "sha256"}.issubset(reader.fieldnames or []):
                audit.error(f"{MANIFEST_NAME}: required columns are path, size_bytes, sha256")
                return listed
            for line, row in enumerate(reader, start=2):
                try:
                    path = safe_manifest_path(audit.root, row["path"])
                    identity = path.relative_to(audit.root).as_posix().casefold()
                    if identity in identities:
                        raise ValueError(f"duplicate manifest path: {row['path']!r}")
                    identities.add(identity)
                    listed.add(path)
                    if path == manifest:
                        raise ValueError("the manifest must not list itself")
                    size = int(row["size_bytes"])
                    expected_hash = row["sha256"]
                    if size < 0 or not SHA256.fullmatch(expected_hash):
                        raise ValueError("invalid size_bytes or sha256")
                    if not path.is_file():
                        raise ValueError(f"missing file: {row['path']}")
                    if path.stat().st_size != size:
                        audit.error(f"Size mismatch: {row['path']}")
                    if digest(path) != expected_hash.lower():
                        audit.error(f"SHA-256 mismatch: {row['path']}")
                    audit.count("manifest files")
                except (OSError, TypeError, ValueError) as exc:
                    audit.error(f"{MANIFEST_NAME}:{line}: {exc}")
    except (OSError, UnicodeError, csv.Error) as exc:
        audit.error(f"Cannot read {MANIFEST_NAME}: {exc}")
    if not listed:
        audit.error(f"{MANIFEST_NAME}: manifest contains no file entries")
    for path in sorted(files - listed - {manifest}):
        audit.error(f"File absent from release manifest: {audit.label(path)}")
    return listed


def decoded_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from decoded_strings(key)
            yield from decoded_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from decoded_strings(item)


def check_markdown_links(audit: Audit, path: Path, text: str) -> None:
    # Fenced examples and inline code are documentation samples, not live links.
    text = re.sub(r"(?ms)^\s*(```|~~~).*?^\s*\1\s*$", "", text)
    text = re.sub(r"`[^`\n]*`", "", text)
    for pattern in (INLINE_LINK, REFERENCE_LINK):
        for match in pattern.finditer(text):
            target = html.unescape(match.group(1) or match.group(2))
            try:
                parsed = urlsplit(target)
                if parsed.scheme or parsed.netloc or not parsed.path:
                    continue
                link_path = unquote(parsed.path).replace("\\", "/")
                candidate = (
                    audit.root / link_path.lstrip("/")
                    if link_path.startswith("/") else path.parent / link_path
                ).resolve()
                if not candidate.is_relative_to(audit.root):
                    audit.error(f"Local link leaves release: {audit.label(path)} -> {target}")
                elif not candidate.exists():
                    audit.error(f"Broken local link: {audit.label(path)} -> {target}")
            except (OSError, ValueError) as exc:
                audit.error(f"Invalid link: {audit.label(path)} -> {target}: {exc}")


def inspect_text(audit: Audit, path: Path) -> None:
    label = audit.label(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
        audit.check_english(html.unescape(text), label)
        if path.suffix.lower() == ".py":
            tree = ast.parse(text, filename=label)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    audit.check_english(node.value, f"{label}:{node.lineno} (decoded Python string)")
            audit.count("Python syntax checks")
        elif path.suffix.lower() == ".json":
            payload = json.loads(text)
            for value in decoded_strings(payload):
                audit.check_english(value, f"{label} (decoded JSON string)")
            audit.count("JSON checks")
        elif path.suffix.lower() == ".md":
            check_markdown_links(audit, path, text)
            audit.count("Markdown link checks")
        audit.count("text files")
    except (OSError, UnicodeError, SyntaxError, ValueError) as exc:
        audit.error(f"Cannot validate text {label}: {exc}")


def inspect_workbook(audit: Audit, path: Path) -> None:
    """Inspect decoded cell/sheet/comment text, excluding standard theme fonts."""
    label = audit.label(path)
    try:
        with ZipFile(path) as archive:
            parts = [
                name for name in archive.namelist()
                if name in {"xl/workbook.xml", "xl/sharedStrings.xml"}
                or (name.startswith("xl/worksheets/") and name.endswith(".xml"))
                or (name.startswith("xl/comments") and name.endswith(".xml"))
                or (name.startswith("xl/threadedComments/") and name.endswith(".xml"))
            ]
            if "xl/workbook.xml" not in parts:
                raise ValueError("missing workbook.xml")
            for part in parts:
                raw = archive.read(part)
                # Wide Usage sheets contain millions of XML nodes. Decoding a
                # part directly covers text and attributes without building a tree.
                if raw.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
                    encoding = "utf-32"
                elif raw.startswith((b"\xff\xfe", b"\xfe\xff")):
                    encoding = "utf-16"
                elif raw.startswith(b"<\x00"):
                    encoding = "utf-16-le"
                elif raw.startswith(b"\x00<"):
                    encoding = "utf-16-be"
                else:
                    declaration = re.search(br"encoding\s*=\s*['\"]([^'\"]+)['\"]", raw[:160])
                    encoding = declaration.group(1).decode("ascii") if declaration else "utf-8-sig"
                text = raw.decode(encoding)
                if HAN.search(html.unescape(text)):
                    audit.error(f"Chinese/Han workbook text found: {label} :: {part}")
        audit.count("workbook text checks")
    except (OSError, BadZipFile, LookupError, ValueError) as exc:
        audit.error(f"Cannot validate workbook {label}: {exc}")


def inspect_npy(path: Path) -> None:
    """Read only the portable NPY header and payload length, without NumPy."""
    with path.open("rb") as handle:
        if handle.read(6) != b"\x93NUMPY":
            raise ValueError("invalid NPY magic")
        version = handle.read(2)
        if version not in {b"\x01\x00", b"\x02\x00", b"\x03\x00"}:
            raise ValueError("unsupported NPY format version")
        length_size = 2 if version == b"\x01\x00" else 4
        length_bytes = handle.read(length_size)
        if len(length_bytes) != length_size:
            raise ValueError("truncated NPY header length")
        length = struct.unpack("<H" if length_size == 2 else "<I", length_bytes)[0]
        if length > 65536:
            raise ValueError("unexpectedly large NPY header")
        header = ast.literal_eval(handle.read(length).decode("utf-8" if version == b"\x03\x00" else "latin1"))
        if header.get("shape") != (744, 36) or header.get("descr") not in {"<f8", ">f8", "=f8"}:
            raise ValueError("expected a 744 x 36 float64 NPY array")
        if header.get("fortran_order") is not False:
            raise ValueError("expected a C-order NPY array")
        if path.stat().st_size - handle.tell() != 744 * 36 * 8:
            raise ValueError("NPY payload length does not match its shape")


def check_coverage(audit: Audit) -> None:
    config_path = audit.root / "Program/Micro/experiment_config_1hour.json"
    try:
        if not config_path.resolve().is_relative_to(audit.root):
            raise ValueError("configuration resolves outside release")
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        members = config["members"]
        months = config["months"]
        if not isinstance(members, dict) or len(members) != 13:
            raise ValueError("release configuration must contain 13 resource seeds")
        if not isinstance(months, list) or len(months) != 6 or len(set(months)) != 6:
            raise ValueError("release configuration must contain six distinct months")
        if not all(isinstance(month, str) and MONTH.fullmatch(month) for month in months):
            raise ValueError("invalid configured month")
        if not all(isinstance(member, str) and re.fullmatch(r"[A-Za-z0-9_]+", member) for member in members):
            raise ValueError("invalid configured resource name")
        data_dir = (config_path.parent / config["data_dir"]).resolve()
        result_dir = (config_path.parent / config["output_root"]).resolve()
        if not all(path.is_relative_to(audit.root) for path in (data_dir, result_dir)):
            raise ValueError("configured input/output directories leave the release")
        random_dir = result_dir / "random"
        for month in months:
            token = month.replace("-", "")
            for member in members:
                required = (
                    data_dir / f"{member}_Usage_{token}.xlsx",
                    result_dir / "rolling" / f"{member}_Rolling_Window_1hour_{token}.xlsx",
                    result_dir / "Classified" / f"classified_{member}_1hour_{token}.xlsx",
                    result_dir / "realized_profit" / f"Realized_Profit_{member}_1hour_{token}.xlsx",
                    result_dir / "realized_match" / f"Realized_Match_{member}_1hour_{token}.xlsx",
                )
                for path in required:
                    if not path.is_file():
                        audit.error(f"Missing configured resource-month file: {audit.label(path)}")
                    else:
                        audit.count("configured workbook paths")
                manifests = list(random_dir.glob(f"{member}_seed*_744x36_1hour_{token}_manifest.json"))
                if len(manifests) != 1:
                    audit.error(f"Expected one random manifest for {member}/{month}; found {len(manifests)}")
                    continue
                manifest = manifests[0]
                try:
                    if not manifest.resolve().is_relative_to(audit.root):
                        raise ValueError("random manifest resolves outside release")
                    metadata = json.loads(manifest.read_text(encoding="utf-8-sig"))
                    expected = {
                        "member": member, "origin_date": f"{month}-01",
                        "shape": [744, 36], "dtype": "float64", "interval_minutes": 60,
                        "layout": "elapsed_hour_by_lookahead_lead",
                    }
                    if any(metadata.get(key) != value for key, value in expected.items()):
                        raise ValueError("random metadata identity or shape mismatch")
                    array = manifest.with_name(manifest.name.removesuffix("_manifest.json") + ".npy")
                    if not array.resolve().is_relative_to(audit.root):
                        raise ValueError("random array resolves outside release")
                    if digest(array) != metadata.get("sha256"):
                        raise ValueError("random NPY SHA-256 mismatch")
                    inspect_npy(array)
                    audit.count("random matrix hashes")
                except (OSError, UnicodeError, SyntaxError, ValueError, AttributeError) as exc:
                    audit.error(f"Invalid random pair {audit.label(manifest)}: {exc}")
    except (OSError, UnicodeError, KeyError, TypeError, ValueError) as exc:
        audit.error(f"Cannot validate configured coverage: {exc}")


def main(argv: list[str] | None = None) -> int:
    started = time.perf_counter()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="release root (default: inferred from this script)")
    parser.add_argument("--skip-manifest", action="store_true", help="skip snapshot byte checks after intentionally regenerating results")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if not root.is_dir():
        parser.error(f"release root does not exist: {root}")
    audit = Audit(root)
    files = inventory(root)
    if not args.skip_manifest:
        files.update(verify_manifest(audit, files))
    print(f"Scanning {len(files)} release files...", flush=True)
    for path in sorted(files):
        if not path.resolve().is_relative_to(root):
            audit.error(f"File resolves outside release: {audit.label(path)}")
            continue
        if not path.is_file():
            continue
        audit.check_english(path.relative_to(root).as_posix(), f"filename {audit.label(path)}")
        if path.suffix.lower() in TEXT_SUFFIXES or path.name in {".gitignore", ".gitattributes", "LICENSE"}:
            inspect_text(audit, path)
        elif path.suffix.lower() == ".xlsx":
            inspect_workbook(audit, path)
    check_coverage(audit)
    for category, count in sorted(audit.counts.items()):
        print(f"{category}: {count}")
    print("Scope: file integrity, syntax, text, local links, coverage, and persisted random hashes.")
    print("Not checked: numerical model results, PDF/raster text, or standard OOXML theme font names.")
    print(f"Elapsed seconds: {time.perf_counter() - started:.2f}")
    if audit.errors:
        for error in audit.errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print(f"FAILED: {len(audit.errors)} issue(s)", file=sys.stderr)
        return 1
    print("PASS" + (" (release manifest checks skipped)" if args.skip_manifest else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
