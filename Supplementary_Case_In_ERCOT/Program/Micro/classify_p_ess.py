"""Classify monthly hourly Rolling results using row-specific bidding data."""

from __future__ import annotations

import argparse
import ast
import math
import re
import sys
from collections import Counter
from copy import copy
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Sequence

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import range_boundaries


from project_paths import PROJECT_ROOT, MICRO_RESULTS_DIR
DEFAULT_ROLLING_DIR = MICRO_RESULTS_DIR / "rolling"
DEFAULT_CLASSIFIED_DIR = MICRO_RESULTS_DIR / "Classified"
ROLLING_WORKBOOK_PATTERN = "*_Rolling_Window_1hour_[0-9][0-9][0-9][0-9][0-9][0-9].xlsx"
ROLLING_WORKBOOK_NAME = re.compile(
    r".+_Rolling_Window_1hour_(?P<year>[0-9]{4})(?P<month>0[1-9]|1[0-2])\.xlsx"
)
STATUS_HEADER = "P_ESS_state"
P_ESS_HEADER = "P_ESS"
CLEARED_MW_HEADER = "Cleared MW"
RESOURCE_HEADER = "Resource Name"
MONTH_HEADER = "Month"
PCMAX_HEADER = "Pcmax"
PDMAX_HEADER = "Pdmax"
REL_TOL = 1e-9
ABS_TOL = 1e-6
MAX_RATE_ABS_TOL = 5e-4
SUMMARY_ORDER = ("MC", "MD", "NU", "DC", "DD", "CC", "CD", "NC")
SKIPPED_ZERO_LIMIT_ROWS = "skipped_zero_limit_rows"
INVALID_FILENAME_CHARACTERS = frozenset('<>:"/\\|?*')
WINDOWS_RESERVED_FILENAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)


@dataclass(frozen=True)
class WorkbookResult:
    """Successful classification result for one member workbook."""

    input_path: Path
    output_path: Path
    resource_name: str
    counts: Counter
    month: str


@dataclass(frozen=True)
class WorkbookFailure:
    """Classification failure isolated to one member workbook."""

    input_path: Path
    message: str


def values_match(left: float, right: float) -> bool:
    """Compare numeric values while allowing spreadsheet round-off."""
    return math.isclose(left, right, rel_tol=REL_TOL, abs_tol=ABS_TOL)


def max_rate_values_match(left: float, right: float) -> bool:
    """Compare power values after the upstream four-decimal rounding."""
    return math.isclose(
        left,
        right,
        rel_tol=REL_TOL,
        abs_tol=MAX_RATE_ABS_TOL,
    )


def as_float(value: Any, *, name: str, excel_row: int) -> float:
    """Convert a worksheet value to a finite float."""
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Row {excel_row}: {name} is not a valid number: {value!r}") from exc
    if not math.isfinite(result):
        raise ValueError(f"Row {excel_row}: {name} is not finite: {value!r}")
    return result


def parse_dsfunction(value: Any, excel_row: int) -> Sequence[Sequence[Any]]:
    """Parse one dsfunction cell with seven core values and optional SOC."""
    if isinstance(value, str):
        try:
            value = ast.literal_eval(value)
        except (SyntaxError, ValueError) as exc:
            raise ValueError(f"Row {excel_row}: dsfunction cannot be parsed: {exc}") from exc
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError(f"Row {excel_row}: dsfunction must be a non-empty 2-D array.")
    for segment_number, segment in enumerate(value, start=1):
        if not isinstance(segment, (list, tuple)) or len(segment) not in (7, 8):
            raise ValueError(
                f"Row {excel_row}: dsfunction segment {segment_number} must contain "
                "7 core values and may contain an eighth SOC value."
            )
    return value


def find_column(headers: list[Any], header_name: str) -> int:
    """Find a unique one-based worksheet column by header."""
    matches = [index for index, value in enumerate(headers, start=1) if value == header_name]
    if len(matches) != 1:
        raise ValueError(
            f"Worksheet header {header_name!r} appears {len(matches)} times; exactly one is required."
        )
    return matches[0]


def select_worksheet(workbook: Any, sheet_name: str | None) -> Any:
    """Select the requested worksheet, or the active sheet when omitted."""
    if sheet_name is None:
        return workbook.active
    if sheet_name in workbook.sheetnames:
        return workbook[sheet_name]
    raise ValueError(
        f"Worksheet {sheet_name!r} not found; available worksheets: "
        f"{', '.join(workbook.sheetnames)}"
    )


def resolve_power_header(headers: list[Any], requested_header: str | None) -> str:
    """Resolve the power column while keeping legacy P_ESS inputs compatible."""
    if requested_header is not None:
        find_column(headers, requested_header)
        return requested_header
    if P_ESS_HEADER in headers:
        find_column(headers, P_ESS_HEADER)
        return P_ESS_HEADER
    find_column(headers, CLEARED_MW_HEADER)
    return CLEARED_MW_HEADER


def validate_resource_name(value: Any, *, input_path: Path) -> str:
    """Validate a resource name before using it as an output filename component."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{input_path.name}: {RESOURCE_HEADER!r} must be a non-empty string.")
    resource_name = value.strip()
    if any(character in INVALID_FILENAME_CHARACTERS for character in resource_name):
        raise ValueError(
            f"{input_path.name}: resource name contains a filename-invalid character: "
            f"{resource_name!r}."
        )
    if resource_name.endswith((" ", ".")):
        raise ValueError(
            f"{input_path.name}: resource name cannot end in a space or period: "
            f"{resource_name!r}."
        )
    if resource_name.split(".", maxsplit=1)[0].upper() in WINDOWS_RESERVED_FILENAMES:
        raise ValueError(
            f"{input_path.name}: resource name is reserved by Windows: {resource_name!r}."
        )
    return resource_name


def find_unique_resource_name(worksheet: Any, input_path: Path) -> str:
    """Return the single Resource Name value represented by a member worksheet."""
    headers = list(next(worksheet.iter_rows(min_row=1, max_row=1, values_only=True)))
    resource_col = find_column(headers, RESOURCE_HEADER)
    names = {
        validate_resource_name(row[0], input_path=input_path)
        for row in worksheet.iter_rows(
            min_row=2,
            min_col=resource_col,
            max_col=resource_col,
            values_only=True,
        )
    }
    if len(names) != 1:
        raise ValueError(
            f"{input_path.name}: expected exactly one {RESOURCE_HEADER!r}, found "
            f"{len(names)}: {sorted(names)}."
        )
    return names.pop()


def row_has_zero_power_limit(pcmax: float, pdmax: float) -> bool:
    """Return whether a row has no available charge or discharge capability."""
    return values_match(pcmax, 0.0) or values_match(pdmax, 0.0)


def classify_p_ess(
    p_ess_value: Any,
    ds_row: Sequence[Any],
    excel_row: int,
    *,
    pcmax: float,
    pdmax: float,
) -> str:
    """Classify one segment using signed row-level Pcmax/Pdmax values."""
    p_ess = as_float(p_ess_value, name="P_ESS", excel_row=excel_row)
    if max_rate_values_match(p_ess, pcmax):
        return "MC"
    if max_rate_values_match(p_ess, pdmax):
        return "MD"
    if values_match(p_ess, 0.0):
        return "NU"

    seventh = as_float(ds_row[6], name="dsfunction segment column 7", excel_row=excel_row)
    if values_match(seventh, 1.0):
        return "DC" if p_ess > 0 else "CC"
    if values_match(seventh, -1.0):
        return "DD" if p_ess > 0 else "CD"
    if values_match(seventh, 0.0):
        fourth = as_float(ds_row[3], name="dsfunction segment column 4", excel_row=excel_row)
        fifth = as_float(ds_row[4], name="dsfunction segment column 5", excel_row=excel_row)
        if values_match(fourth, fifth):
            return "DD" if p_ess > 0 else "CC"
        if p_ess > 0:
            return "DC" if fourth > fifth else "DD"
        return "CC" if fourth > fifth else "CD"
    return "NC"


def classify_p_ess_from_dsfunction(
    p_ess_value: Any,
    dsfunction_value: Any,
    excel_row: int,
    *,
    pcmax: float,
    pdmax: float,
) -> str:
    """Match P_ESS to its unique dsfunction segment and classify it."""
    p_ess = as_float(p_ess_value, name="P_ESS", excel_row=excel_row)
    if (
        max_rate_values_match(p_ess, pcmax)
        or max_rate_values_match(p_ess, pdmax)
        or values_match(p_ess, 0.0)
    ):
        return classify_p_ess(p_ess, (), excel_row, pcmax=pcmax, pdmax=pdmax)

    dsfunction = parse_dsfunction(dsfunction_value, excel_row)
    matches: list[Sequence[Any]] = []
    for segment_number, segment in enumerate(dsfunction, start=1):
        candidate = as_float(
            segment[2],
            name=f"dsfunction segment {segment_number}, column 3",
            excel_row=excel_row,
        )
        if values_match(p_ess, candidate):
            matches.append(segment)
    if not matches:
        raise ValueError(
            f"Row {excel_row}: P_ESS={p_ess:g} matched no rows in dsfunction column 3."
        )
    states = {
        classify_p_ess(p_ess, segment, excel_row, pcmax=pcmax, pdmax=pdmax)
        for segment in matches
    }
    if len(states) != 1:
        raise ValueError(
            f"Row {excel_row}: P_ESS={p_ess:g} matched multiple segments with "
            f"different states: {sorted(states)}."
        )
    return states.pop()


def default_output_path(input_path: Path) -> Path:
    """Return the standalone classifier's default output path."""
    return input_path.with_name(f"{input_path.stem}_classified{input_path.suffix}")


def expand_auto_filter_for_insert(worksheet: Any, inserted_col: int) -> None:
    """Keep an existing auto-filter aligned after a column insertion."""
    filter_ref = worksheet.auto_filter.ref
    if not filter_ref:
        return
    min_col, min_row, max_col, max_row = range_boundaries(filter_ref)
    if inserted_col < min_col:
        min_col += 1
        max_col += 1
    elif inserted_col <= max_col + 1:
        max_col += 1
    worksheet.auto_filter.ref = (
        f"{get_column_letter(min_col)}{min_row}:"
        f"{get_column_letter(max_col)}{max_row}"
    )


def insert_status_column(worksheet: Any, power_col: int) -> int:
    """Insert P_ESS_state to the right of power while preserving display metadata."""
    status_col = power_col + 1
    original_max_col = worksheet.max_column
    original_dimensions = {
        column: copy(worksheet.column_dimensions[get_column_letter(column)])
        for column in range(status_col, original_max_col + 1)
    }
    worksheet.insert_cols(status_col, amount=1)

    # openpyxl moves cells but not column dimensions, so shift those explicitly.
    for column, dimension in reversed(original_dimensions.items()):
        target_letter = get_column_letter(column + 1)
        target_dimension = worksheet.column_dimensions[target_letter]
        target_dimension.width = dimension.width
        target_dimension.hidden = dimension.hidden
        target_dimension.bestFit = dimension.bestFit
        target_dimension.outlineLevel = dimension.outlineLevel
        target_dimension.collapsed = dimension.collapsed
        target_dimension._style = copy(dimension._style)

    source_dimension = worksheet.column_dimensions[get_column_letter(power_col)]
    status_dimension = worksheet.column_dimensions[get_column_letter(status_col)]
    status_dimension.hidden = source_dimension.hidden
    status_dimension.bestFit = source_dimension.bestFit
    status_dimension.outlineLevel = source_dimension.outlineLevel
    status_dimension.collapsed = source_dimension.collapsed
    status_dimension._style = copy(source_dimension._style)
    expand_auto_filter_for_insert(worksheet, status_col)
    return status_col


def prepare_status_column(worksheet: Any, power_header: str) -> tuple[int, int]:
    """Validate or create the status column and return power/status indices."""
    headers = [cell.value for cell in worksheet[1]]
    power_col = find_column(headers, power_header)
    status_col = power_col + 1
    existing = [index for index, value in enumerate(headers, start=1) if value == STATUS_HEADER]
    if existing and existing != [status_col]:
        raise ValueError(
            f"Existing {STATUS_HEADER!r} must be immediately right of {power_header!r}."
        )
    if not existing:
        status_col = insert_status_column(worksheet, power_col)

    for row in range(1, worksheet.max_row + 1):
        source_cell = worksheet.cell(row=row, column=power_col)
        target_cell = worksheet.cell(row=row, column=status_col)
        if source_cell.has_style:
            target_cell._style = copy(source_cell._style)
        if source_cell.hyperlink:
            target_cell.hyperlink = copy(source_cell.hyperlink)

    source_letter = get_column_letter(power_col)
    status_letter = get_column_letter(status_col)
    worksheet.column_dimensions[status_letter].width = max(
        worksheet.column_dimensions[source_letter].width or 13.0,
        float(len(STATUS_HEADER) + 2),
    )
    worksheet.cell(row=1, column=status_col).value = STATUS_HEADER
    return power_col, status_col


def classify_worksheet(worksheet: Any, requested_power_header: str | None = None) -> Counter:
    """Classify every eligible row in one validated worksheet."""
    headers = [cell.value for cell in worksheet[1]]
    power_header = resolve_power_header(headers, requested_power_header)
    find_column(headers, "dsfunction")
    find_column(headers, PCMAX_HEADER)
    find_column(headers, PDMAX_HEADER)
    prepare_status_column(worksheet, power_header)

    headers = [cell.value for cell in worksheet[1]]
    dsfunction_col = find_column(headers, "dsfunction")
    power_col = find_column(headers, power_header)
    pcmax_col = find_column(headers, PCMAX_HEADER)
    pdmax_col = find_column(headers, PDMAX_HEADER)
    status_col = find_column(headers, STATUS_HEADER)

    counts: Counter = Counter()
    for excel_row in range(2, worksheet.max_row + 1):
        pcmax = as_float(
            worksheet.cell(excel_row, pcmax_col).value,
            name=PCMAX_HEADER,
            excel_row=excel_row,
        )
        pdmax = as_float(
            worksheet.cell(excel_row, pdmax_col).value,
            name=PDMAX_HEADER,
            excel_row=excel_row,
        )
        target_cell = worksheet.cell(row=excel_row, column=status_col)
        if row_has_zero_power_limit(pcmax, pdmax):
            target_cell.value = None
            counts[SKIPPED_ZERO_LIMIT_ROWS] += 1
            continue
        state = classify_p_ess_from_dsfunction(
            worksheet.cell(excel_row, power_col).value,
            worksheet.cell(excel_row, dsfunction_col).value,
            excel_row,
            pcmax=pcmax,
            pdmax=pdmax,
        )
        target_cell.value = state
        target_cell.number_format = "@"
        counts[state] += 1
    return counts


def save_workbook_atomically(workbook: Any, output_path: Path) -> None:
    """Replace one output only after its complete workbook has been saved."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        prefix=f".{output_path.stem}.",
        suffix=".tmp.xlsx",
        dir=output_path.parent,
        delete=False,
    ) as temporary_file:
        temporary_path = Path(temporary_file.name)
    try:
        workbook.save(temporary_path)
        temporary_path.replace(output_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def classify_workbook(
    input_path: Path,
    output_path: Path,
    sheet_name: str | None = None,
    *,
    power_header: str | None = None,
) -> Counter:
    """Add/update P_ESS_state and atomically save a full workbook copy."""
    if input_path.resolve() == output_path.resolve():
        raise ValueError("Classification output must differ from its input workbook.")
    if not input_path.is_file():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    workbook = load_workbook(input_path, data_only=False)
    try:
        worksheet = select_worksheet(workbook, sheet_name)
        counts = classify_worksheet(worksheet, power_header)
        save_workbook_atomically(workbook, output_path)
        return counts
    finally:
        workbook.close()


def read_member_resource_name(input_path: Path, sheet_name: str | None) -> str:
    """Read and validate the authoritative resource name without changing a workbook."""
    workbook = load_workbook(input_path, read_only=True, data_only=False)
    try:
        worksheet = select_worksheet(workbook, sheet_name)
        return find_unique_resource_name(worksheet, input_path)
    finally:
        workbook.close()


def read_monthly_identity(input_path: Path, sheet_name: str | None) -> tuple[str, str]:
    """Validate the filename month against every row before choosing an output."""
    match = ROLLING_WORKBOOK_NAME.fullmatch(input_path.name)
    if match is None or match["year"] == "0000":
        raise ValueError(f"{input_path.name}: expected a valid YYYYMM hourly filename.")
    month = f"{match['year']}-{match['month']}"
    workbook = load_workbook(input_path, read_only=True, data_only=False)
    try:
        worksheet = select_worksheet(workbook, sheet_name)
        resource_name = find_unique_resource_name(worksheet, input_path)
        headers = list(next(worksheet.iter_rows(min_row=1, max_row=1, values_only=True)))
        month_col = find_column(headers, MONTH_HEADER)
        for excel_row, (value,) in enumerate(
            worksheet.iter_rows(
                min_row=2, min_col=month_col, max_col=month_col, values_only=True
            ),
            start=2,
        ):
            if value != month:
                raise ValueError(
                    f"{input_path.name}: Row {excel_row}: Month={value!r} "
                    f"does not match filename month {month!r}."
                )
        return resource_name, month
    finally:
        workbook.close()


def discover_rolling_workbooks(input_dir: Path) -> list[Path]:
    """Return monthly hourly workbooks, excluding legacy files and subdirectories."""
    if not input_dir.is_dir():
        raise NotADirectoryError(f"Rolling input directory not found: {input_dir}")
    workbooks = sorted(
        path for path in input_dir.glob(ROLLING_WORKBOOK_PATTERN) if path.is_file()
    )
    if not workbooks:
        raise FileNotFoundError(
            f"No member workbooks matching {ROLLING_WORKBOOK_PATTERN!r} in {input_dir}."
        )
    return workbooks


def classify_rolling_directory(
    input_dir: Path,
    output_dir: Path,
    sheet_name: str | None = None,
) -> tuple[list[WorkbookResult], list[WorkbookFailure]]:
    """Classify all rolling members, isolating failures to individual workbooks."""
    successes: list[WorkbookResult] = []
    failures: list[WorkbookFailure] = []
    claimed_outputs: dict[str, Path] = {}

    for input_path in discover_rolling_workbooks(input_dir):
        try:
            resource_name, month = read_monthly_identity(input_path, sheet_name)
            output_path = output_dir / (
                f"classified_{resource_name}_1hour_{month.replace('-', '')}.xlsx"
            )
            output_key = str(output_path.resolve()).casefold()
            if output_key in claimed_outputs:
                raise ValueError(
                    f"Output collision with {claimed_outputs[output_key].name}: {output_path.name}."
                )
            claimed_outputs[output_key] = input_path
            counts = classify_workbook(
                input_path,
                output_path,
                sheet_name,
                power_header=CLEARED_MW_HEADER,
            )
            successes.append(
                WorkbookResult(
                    input_path=input_path,
                    output_path=output_path,
                    resource_name=resource_name,
                    counts=counts,
                    month=month,
                )
            )
        except Exception as exc:
            failures.append(WorkbookFailure(input_path=input_path, message=str(exc)))
    return successes, failures


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Classify monthly hourly rolling workbooks in batch, or one P_ESS/Cleared MW workbook "
            "when --input is supplied."
        )
    )
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_ROLLING_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_CLASSIFIED_DIR)
    parser.add_argument("--sheet")
    return parser


def print_counts(counts: Counter) -> None:
    """Print classified totals while reporting blank zero-limit rows separately."""
    print(f"  Analyzed records: {sum(counts[state] for state in SUMMARY_ORDER)}")
    print(f"  Zero-limit rows left blank: {counts[SKIPPED_ZERO_LIMIT_ROWS]}")
    for state in SUMMARY_ORDER:
        print(f"  P_ESS_state={state}: {counts[state]}")


def main() -> int:
    parser = build_parser()
    arguments = parser.parse_args()
    if arguments.output is not None and arguments.input is None:
        parser.error("--output requires --input")

    if arguments.input is not None:
        output_path = arguments.output or default_output_path(arguments.input)
        counts = classify_workbook(arguments.input, output_path, arguments.sheet)
        print(f"Done: {output_path}")
        print_counts(counts)
        return 0

    successes, failures = classify_rolling_directory(
        arguments.input_dir,
        arguments.output_dir,
        arguments.sheet,
    )
    combined_counts: Counter = Counter()
    for result in successes:
        combined_counts.update(result.counts)
        print(f"Done [{result.resource_name}, {result.month}]: {result.output_path}")
        print_counts(result.counts)

    print(f"Batch complete: {len(successes)} succeeded, {len(failures)} failed.")
    print_counts(combined_counts)
    for failure in failures:
        print(f"Failed [{failure.input_path.name}]: {failure.message}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        raise SystemExit(f"Error: {exc}") from None
