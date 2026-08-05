"""Export the monthly P_ESS_state distribution for each hour of day."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


SCRIPT_DIR = Path(__file__).resolve().parent
INPUT_DIR = SCRIPT_DIR / "input"
OUTPUT_DIR = SCRIPT_DIR / "output"
SOURCE = INPUT_DIR / "dsfunction_May2025_exact_V5_k20_classified.xlsx"
OUTPUT = OUTPUT_DIR / "P_ESS_state_hourly_counts_May2025.xlsx"
SOURCE_SHEET = "May2025"
STATE_ORDER = ("MC", "MD", "NU", "DC", "DD", "CC", "CD", "NC")


def read_hourly_counts() -> dict[int, Counter]:
    workbook = load_workbook(SOURCE, data_only=True, read_only=True)
    if SOURCE_SHEET not in workbook.sheetnames:
        raise ValueError(f"Worksheet not found: {SOURCE_SHEET}")
    worksheet = workbook[SOURCE_SHEET]

    headers = {cell.value: cell.column for cell in next(worksheet.iter_rows(max_row=1))}
    for required in ("time", "P_ESS_state"):
        if required not in headers:
            raise ValueError(f"Source worksheet is missing column: {required}")

    hourly_counts = {hour: Counter() for hour in range(24)}
    record_count = 0
    for row in worksheet.iter_rows(min_row=2, values_only=True):
        timestamp = row[headers["time"] - 1]
        state = row[headers["P_ESS_state"] - 1]
        if timestamp is None and state is None:
            continue
        if not hasattr(timestamp, "hour"):
            raise ValueError(f"Invalid value in time column: {timestamp!r}")
        if state not in STATE_ORDER:
            raise ValueError(f"Undefined state in P_ESS_state: {state!r}")
        hourly_counts[timestamp.hour][state] += 1
        record_count += 1

    if record_count != 744:
        raise ValueError(f"Read {record_count} records; expected 744")
    for hour, counts in hourly_counts.items():
        if sum(counts.values()) != 31:
            raise ValueError(
                f"{hour:02d}:00 contains {sum(counts.values())} records; expected 31"
            )
    return hourly_counts


def build_workbook(hourly_counts: dict[int, Counter]) -> Workbook:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Hourly Counts"
    worksheet.sheet_view.showGridLines = False
    worksheet.freeze_panes = "B2"

    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    state_fill = PatternFill("solid", fgColor="D9EAF7")
    total_fill = PatternFill("solid", fgColor="E2F0D9")
    body_font = Font(name="Calibri", size=11, color="1F1F1F")
    thin_blue = Side(style="thin", color="9EBDD3")
    medium_blue = Side(style="medium", color="4472C4")

    worksheet.cell(1, 1, "P_ESS_state")
    for hour in range(24):
        worksheet.cell(1, hour + 2, f"{hour:02d}:00")

    for state_row, state in enumerate(STATE_ORDER, start=2):
        worksheet.cell(state_row, 1, state)
        for hour in range(24):
            worksheet.cell(state_row, hour + 2, hourly_counts[hour][state])

    data_end_row = len(STATE_ORDER) + 1
    total_row = data_end_row + 1
    worksheet.cell(total_row, 1, "Total")
    for hour in range(24):
        column_letter = get_column_letter(hour + 2)
        worksheet.cell(
            total_row,
            hour + 2,
            f"=SUM({column_letter}2:{column_letter}{data_end_row})",
        )

    for cell in worksheet[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = Border(bottom=medium_blue)

    for row in worksheet.iter_rows(
        min_row=2,
        max_row=data_end_row,
        min_col=1,
        max_col=25,
    ):
        for cell in row:
            cell.font = body_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.number_format = "0"
        row[0].fill = state_fill
        row[0].font = Font(name="Calibri", size=11, bold=True, color="1F1F1F")
        row[0].border = Border(right=thin_blue)

    for cell in worksheet[total_row]:
        cell.fill = total_fill
        cell.font = Font(name="Calibri", size=11, bold=True, color="1F1F1F")
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.number_format = "0"
        cell.border = Border(top=medium_blue)

    worksheet.conditional_formatting.add(
        f"B2:Y{data_end_row}",
        ColorScaleRule(
            start_type="min",
            start_color="FFF2CC",
            mid_type="percentile",
            mid_value=50,
            mid_color="9DC3E6",
            end_type="max",
            end_color="4472C4",
        ),
    )

    worksheet.column_dimensions["A"].width = 15
    for column in range(2, 26):
        worksheet.column_dimensions[get_column_letter(column)].width = 8
    worksheet.row_dimensions[1].height = 24
    for row in range(2, total_row + 1):
        worksheet.row_dimensions[row].height = 21

    worksheet.auto_filter.ref = f"A1:Y{data_end_row}"
    worksheet.print_title_rows = "1:1"
    worksheet.page_setup.orientation = "landscape"
    worksheet.page_setup.fitToWidth = 1
    worksheet.page_setup.fitToHeight = 1
    worksheet.sheet_properties.pageSetUpPr.fitToPage = True

    worksheet.cell(total_row + 2, 1, "Data Source")
    worksheet.cell(total_row + 2, 2, SOURCE.name)
    worksheet.cell(total_row + 3, 1, "Aggregation Method")
    worksheet.cell(
        total_row + 3,
        2,
        "Counts grouped by hour of time and P_ESS_state",
    )
    for row in range(total_row + 2, total_row + 4):
        worksheet.cell(row, 1).font = Font(name="Calibri", size=10, bold=True, color="666666")
        worksheet.cell(row, 2).font = Font(name="Calibri", size=10, color="666666")

    workbook.calculation.fullCalcOnLoad = True
    workbook.calculation.forceFullCalc = True
    return workbook


def main() -> None:
    hourly_counts = read_hourly_counts()
    workbook = build_workbook(hourly_counts)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(OUTPUT)
    print(f"Exported: {OUTPUT}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        raise SystemExit(f"Error: {exc}") from None
