"""Split Page 1 of CAISO_NG.xlsx into one tidy XLSX file per month.

By default this splits ``Data/CAISO_NG_2023.xlsx``; pass ``--source`` to split
another year's workbook.
"""

import argparse
from pathlib import Path

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = BASE_DIR/ "Data"

DEFAULT_SOURCE = DEFAULT_DATA_DIR / "CAISO_NG_2023.xlsx"
DEFAULT_OUTPUT_DIR = BASE_DIR / "Month_Agg"
DEFAULT_SHEET = "Page1_GenFuel"

MONTHS = {
    1: "January",
    2: "February",
    3: "March",
    4: "April",
    5: "May",
    6: "June",
    7: "July",
    8: "August",
    9: "September",
    10: "October",
    11: "November",
    12: "December",
}

MONTHLY_METRICS = (
    "Quantity",
    "Elec_Quantity",
    "MMBtuPer_Unit",
    "Tot_MMBtu",
    "Elec_MMBtu",
    "Netgen",
)

ANNUAL_TOTAL_COLUMNS = {
    "Total Fuel Consumption\nQuantity",
    "Electric Fuel Consumption\nQuantity",
    "Total Fuel Consumption\nMMBtu",
    "Elec Fuel Consumption\nMMBtu",
    "Net Generation\n(Megawatthours)",
}


def clean_header(column: object) -> str:
    """Make output headers single-line while retaining the original wording."""
    return " ".join(str(column).replace("\n", " ").split())


def split_monthly(source: Path, output_dir: Path, sheet_name: str) -> list[Path]:
    if not source.exists():
        raise FileNotFoundError(f"Input file not found: {source}")

    frame = pd.read_excel(source, sheet_name=sheet_name)
    if "YEAR" not in frame.columns:
        raise KeyError(f"{sheet_name} is missing the YEAR column")

    years = pd.to_numeric(frame["YEAR"], errors="coerce").dropna().astype(int).unique()
    if len(years) != 1:
        raise ValueError(
            "The input must contain exactly one year; detected: "
            f"{sorted(years.tolist())}"
        )
    year = int(years[0])

    monthly_source_columns = {
        f"{metric}\n{month_name}"
        for metric in MONTHLY_METRICS
        for month_name in MONTHS.values()
    }
    missing = sorted(monthly_source_columns - set(frame.columns))
    if missing:
        raise KeyError(f"Missing monthly columns: {missing}")

    static_columns = [
        column
        for column in frame.columns
        if column not in monthly_source_columns
        and column not in ANNUAL_TOTAL_COLUMNS
        and column != "YEAR"
    ]

    output_dir.mkdir(parents=True, exist_ok=True)
    generated_files: list[Path] = []

    for month_number, month_name in MONTHS.items():
        month_columns = [f"{metric}\n{month_name}" for metric in MONTHLY_METRICS]
        monthly = frame[static_columns + month_columns].copy()
        monthly.insert(0, "MONTH", month_number)
        monthly.insert(0, "YEAR", year)

        rename_map = {source_column: metric for source_column, metric in zip(month_columns, MONTHLY_METRICS)}
        monthly = monthly.rename(columns=rename_map)
        monthly.columns = [clean_header(column) for column in monthly.columns]

        output_file = output_dir / f"CAISO_NG_{year}_{month_number:02d}.xlsx"
        monthly.to_excel(output_file, index=False, engine="openpyxl")
        generated_files.append(output_file)

    return generated_files


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Split CAISO natural-gas Page 1 data into monthly XLSX files."
    )
    parser.add_argument(
        "--source", type=Path, default=DEFAULT_SOURCE, help="Input Excel file."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for monthly XLSX output files.",
    )
    parser.add_argument(
        "--sheet", default=DEFAULT_SHEET, help="Worksheet to process."
    )
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    files = split_monthly(arguments.source.resolve(), arguments.output_dir.resolve(), arguments.sheet)
    print(f"Created {len(files)} files in: {arguments.output_dir.resolve()}")
    for file in files:
        print(file.name)
