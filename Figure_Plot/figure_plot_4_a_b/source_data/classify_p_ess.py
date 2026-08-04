"""Classify P_ESS values using the row-specific dsfunction array.

The script keeps the source workbook unchanged and writes a classified copy.
"""

from __future__ import annotations

import argparse
import ast
import math
from collections import Counter
from copy import copy
from pathlib import Path
from typing import Any, Sequence

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter


DEFAULT_INPUT = Path("dsfunction_May2025.xlsx")
STATUS_HEADER = "P_ESS_state"
REL_TOL = 1e-9
ABS_TOL = 1e-6
SUMMARY_ORDER = ("MC", "MD", "NU", "DC", "DD", "CC", "CD", "NC")


def values_match(left: float, right: float) -> bool:
    """Compare numeric values while allowing small spreadsheet round-off."""
    return math.isclose(left, right, rel_tol=REL_TOL, abs_tol=ABS_TOL)


def parse_dsfunction(value: Any, excel_row: int) -> Sequence[Sequence[Any]]:
    """Parse and validate one dsfunction cell."""
    if isinstance(value, str):
        try:
            value = ast.literal_eval(value)
        except (SyntaxError, ValueError) as exc:
            raise ValueError(
                f"Row {excel_row} in the worksheet: dsfunction cannot be parsed: {exc}"
            ) from exc

    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError(f"Row {excel_row}: dsfunction must be a non-empty 2-D array.")

    for ds_row_number, ds_row in enumerate(value, start=1):
        if not isinstance(ds_row, (list, tuple)) or len(ds_row) != 7:
            raise ValueError(
                f"Row {excel_row}: dsfunction segment {ds_row_number} "
                "must contain exactly 7 numbers."
            )
    return value


def as_float(value: Any, *, name: str, excel_row: int) -> float:
    """Convert a cell or dsfunction element to a finite float."""
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Row {excel_row}: {name} is not a valid number: {value!r}") from exc

    if not math.isfinite(result):
        raise ValueError(f"Row {excel_row}: {name} is not a finite number: {value!r}")
    return result


def classify_p_ess(p_ess_value: Any, dsfunction_value: Any, excel_row: int) -> str:
    """Return the requested P_ESS state label for one worksheet row."""
    p_ess = as_float(p_ess_value, name="P_ESS", excel_row=excel_row)

    if values_match(p_ess, 1860.0):
        return "MD"
    if values_match(p_ess, -1860.0):
        return "MC"
    if values_match(p_ess, 0.0):
        return "NU"

    dsfunction = parse_dsfunction(dsfunction_value, excel_row)
    matches = []
    for ds_row_number, ds_row in enumerate(dsfunction, start=1):
        candidate = as_float(
            ds_row[2],
            name=f"dsfunction segment {ds_row_number}, column 3",
            excel_row=excel_row,
        )
        if values_match(p_ess, candidate):
            matches.append((ds_row_number, ds_row))

    if len(matches) != 1:
        raise ValueError(
            f"Row {excel_row}: P_ESS={p_ess:g} matched {len(matches)} rows in "
            f"dsfunction column 3; exactly 1 match expected."
        )

    ds_row_number, matched_row = matches[0]
    seventh = as_float(
        matched_row[6],
        name=f"dsfunction segment {ds_row_number}, column 7",
        excel_row=excel_row,
    )

    if values_match(seventh, 1.0):
        return "DC" if p_ess > 0 else "CC"
    if values_match(seventh, -1.0):
        return "DD" if p_ess > 0 else "CD"

    if values_match(seventh, 0.0):
        fourth = as_float(
            matched_row[3],
            name=f"dsfunction segment {ds_row_number}, column 4",
            excel_row=excel_row,
        )
        fifth = as_float(
            matched_row[4],
            name=f"dsfunction segment {ds_row_number}, column 5",
            excel_row=excel_row,
        )

        if values_match(fourth, fifth):
            return "DD" if p_ess > 0 else "CC"
        if p_ess > 0:
            return "DC" if fourth > fifth else "DD"
        return "CC" if fourth > fifth else "CD"

    return "NC"


def find_column(headers: list[Any], header_name: str) -> int:
    """Find a unique 1-based worksheet column by its row-1 header."""
    matches = [index for index, value in enumerate(headers, start=1) if value == header_name]
    if len(matches) != 1:
        raise ValueError(
            f"Worksheet header {header_name!r} appears {len(matches)} times; "
            f"exactly one is required."
        )
    return matches[0]


def default_output_path(input_path: Path) -> Path:
    return input_path.with_name(f"{input_path.stem}_classified{input_path.suffix}")


def classify_workbook(input_path: Path, output_path: Path, sheet_name: str | None) -> Counter:
    """Add/update P_ESS_state and save a classified workbook copy."""
    if input_path.resolve() == output_path.resolve():
        raise ValueError("Output file must differ from the input file to avoid overwriting the source data.")
    if not input_path.is_file():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    workbook = load_workbook(input_path, data_only=False)
    if sheet_name is None:
        worksheet = workbook.active
    elif sheet_name in workbook.sheetnames:
        worksheet = workbook[sheet_name]
    else:
        raise ValueError(
            f"Worksheet {sheet_name!r} not found; available worksheets: "
            f"{', '.join(workbook.sheetnames)}"
        )

    headers = [cell.value for cell in worksheet[1]]
    dsfunction_col = find_column(headers, "dsfunction")
    p_ess_col = find_column(headers, "P_ESS")
    status_col = p_ess_col + 1

    existing_status_columns = [
        index for index, value in enumerate(headers, start=1) if value == STATUS_HEADER
    ]
    if existing_status_columns:
        if existing_status_columns != [status_col]:
            raise ValueError(
                f"Existing {STATUS_HEADER!r} column is not immediately to the "
                f"right of P_ESS; cannot update safely."
            )
    else:
        worksheet.insert_cols(status_col, amount=1)

    # Match the adjacent P_ESS column's style without altering source data.
    for excel_row in range(1, worksheet.max_row + 1):
        source_cell = worksheet.cell(row=excel_row, column=p_ess_col)
        target_cell = worksheet.cell(row=excel_row, column=status_col)
        if source_cell.has_style:
            target_cell._style = copy(source_cell._style)
        if source_cell.hyperlink:
            target_cell.hyperlink = copy(source_cell.hyperlink)

    source_letter = get_column_letter(p_ess_col)
    status_letter = get_column_letter(status_col)
    source_width = worksheet.column_dimensions[source_letter].width or 13.0
    worksheet.column_dimensions[status_letter].width = max(
        source_width, float(len(STATUS_HEADER) + 2)
    )
    worksheet.cell(row=1, column=status_col).value = STATUS_HEADER

    counts: Counter = Counter()
    for excel_row in range(2, worksheet.max_row + 1):
        state = classify_p_ess(
            worksheet.cell(row=excel_row, column=p_ess_col).value,
            worksheet.cell(row=excel_row, column=dsfunction_col).value,
            excel_row,
        )
        target_cell = worksheet.cell(row=excel_row, column=status_col)
        target_cell.value = state
        target_cell.number_format = "@"
        counts[state] += 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_path)
    return counts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Classify each row's P_ESS using its dsfunction array and "
        "write P_ESS_state to the right of P_ESS."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"Input Excel workbook (default: {DEFAULT_INPUT})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output Excel workbook (default: input name suffixed with _classified)",
    )
    parser.add_argument(
        "--sheet",
        help="Worksheet to process (default: active worksheet)",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    output_path = args.output or default_output_path(args.input)
    counts = classify_workbook(args.input, output_path, args.sheet)

    print(f"Done: {output_path}")
    print(f"Total records: {sum(counts.values())}")
    for state in SUMMARY_ORDER:
        print(f"P_ESS_state={state}: {counts[state]}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        raise SystemExit(f"Error: {exc}") from None
