"""Add an hourly dsfunction state interval-width array."""

from __future__ import annotations

import ast
from collections import Counter
from copy import copy
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from classify_p_ess import as_float, classify_p_ess, values_match


SOURCE = Path("dsfunction_May2025_exact_V5_k20_classified.xlsx")
STATE_ORDER = ("CC", "CD", "NU", "DC", "DD")
OUTPUT_HEADER = "dsfunction_state_width"
LEGACY_OUTPUT_HEADERS = ("dsfunction_state_share", "dsfunction_state_array")


def find_column(headers: list[object], header: str) -> int:
    matches = [index for index, value in enumerate(headers, start=1) if value == header]
    if len(matches) != 1:
        raise ValueError(f"Header {header!r} appears {len(matches)} times; exactly one is required.")
    return matches[0]


def format_width(value: float) -> str:
    """Format one interval width compactly while retaining analysis-grade precision."""
    if abs(value) < 5e-13:
        return "0"
    if abs(value - 1.0) < 5e-13:
        return "1"
    return f"{value:.10f}".rstrip("0").rstrip(".")


def state_width_array(
    dsfunction_value: object,
    excel_row: int,
) -> tuple[str, list[str], list[float]]:
    if not isinstance(dsfunction_value, str):
        raise ValueError(f"Row {excel_row}: dsfunction is not text.")
    dsfunction = ast.literal_eval(dsfunction_value)
    states: list[str] = []
    widths = Counter({state: 0.0 for state in STATE_ORDER})
    for ds_row_number, ds_row in enumerate(dsfunction, start=1):
        third = as_float(
            ds_row[2],
            name=f"dsfunction segment {ds_row_number}, column 3",
            excel_row=excel_row,
        )
        fourth = as_float(
            ds_row[3],
            name=f"dsfunction segment {ds_row_number}, column 4",
            excel_row=excel_row,
        )
        fifth = as_float(
            ds_row[4],
            name=f"dsfunction segment {ds_row_number}, column 5",
            excel_row=excel_row,
        )
        sixth = as_float(
            ds_row[5],
            name=f"dsfunction segment {ds_row_number}, column 6",
            excel_row=excel_row,
        )
        seventh = as_float(
            ds_row[6],
            name=f"dsfunction segment {ds_row_number}, column 7",
            excel_row=excel_row,
        )
        if all(values_match(value, 0.0) for value in (fourth, fifth, sixth)):
            state = "mB" #marginal boundary
        else:
            state = classify_p_ess(third, dsfunction, excel_row)
            if values_match(seventh, 0.0) and values_match(fourth - fifth, 0.0):
                if third > 0:
                    state = "DD"
                elif third < 0:
                    state = "CC"
            if (
                state == "NU"
                and values_match(fourth, 0.0)
                and values_match(fifth, 0.0)
                and values_match(sixth, 1.0)
                and (
                    values_match(seventh, 1.0)
                    or values_match(seventh, -1.0)
                )
            ):
                state = "mB"
        lower = as_float(
            ds_row[0],
            name=f"dsfunction segment {ds_row_number}, column 1",
            excel_row=excel_row,
        )
        upper = as_float(
            ds_row[1],
            name=f"dsfunction segment {ds_row_number}, column 2",
            excel_row=excel_row,
        )
        width = upper - lower
        if width <= 0:
            raise ValueError(
                f"Row {excel_row}: dsfunction segment {ds_row_number} must have a "
                "positive interval width."
            )
        states.append(state)
        if state in STATE_ORDER:
            widths[state] += width

    state_widths = [widths[state] for state in STATE_ORDER]
    array = "[" + ",".join(format_width(width) for width in state_widths) + "]"
    return array, states, state_widths


def main() -> None:
    if not SOURCE.is_file():
        raise FileNotFoundError(f"Input file not found: {SOURCE}")

    workbook = load_workbook(SOURCE, data_only=False)
    worksheet = workbook.active
    headers = [cell.value for cell in worksheet[1]]
    dsfunction_col = find_column(headers, "dsfunction")

    # Preserve the established workbook layout and write the state-width
    # output to column 6.
    output_col = 6
    existing_headers = [
        index
        for index, value in enumerate(headers, start=1)
        if value in (OUTPUT_HEADER, *LEGACY_OUTPUT_HEADERS)
    ]
    if existing_headers and existing_headers != [output_col]:
        raise ValueError(
            f"Existing {OUTPUT_HEADER!r} column is not in column {output_col}; "
            "cannot update safely."
        )
    if worksheet.max_column >= output_col:
        current_header = worksheet.cell(1, output_col).value
        if current_header not in (None, OUTPUT_HEADER, *LEGACY_OUTPUT_HEADERS):
            # Make room instead of overwriting existing column-6 data.
            worksheet.insert_cols(output_col, amount=1)

    style_source_col = 4 if worksheet.max_column >= 4 else 3
    for excel_row in range(1, worksheet.max_row + 1):
        source_cell = worksheet.cell(excel_row, style_source_col)
        target_cell = worksheet.cell(excel_row, output_col)
        if source_cell.has_style:
            target_cell._style = copy(source_cell._style)
        if source_cell.hyperlink:
            target_cell.hyperlink = copy(source_cell.hyperlink)

    worksheet.cell(1, output_col).value = OUTPUT_HEADER
    output_letter = get_column_letter(output_col)
    worksheet.column_dimensions[output_letter].width = 60

    segment_state_counts: Counter[str] = Counter()
    nc_rows: list[int] = []
    for excel_row in range(2, worksheet.max_row + 1):
        value, states, _ = state_width_array(
            worksheet.cell(excel_row, dsfunction_col).value,
            excel_row,
        )
        cell = worksheet.cell(excel_row, output_col)
        cell.value = value
        cell.number_format = "@"
        segment_state_counts.update(states)
        if "mB" in states:
            nc_rows.append(excel_row)

    temporary = SOURCE.with_name(f"{SOURCE.stem}.tmp{SOURCE.suffix}")
    workbook.save(temporary)
    temporary.replace(SOURCE)

    print(f"Updated: {SOURCE}")
    print(f"Records: {worksheet.max_row - 1}")
    print(f"Output state order: {STATE_ORDER}")
    print(f"Segment state counts: {dict(segment_state_counts)}")
    print(f"Worksheet rows containing mB: {nc_rows}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        raise SystemExit(f"Error: {exc}") from None
