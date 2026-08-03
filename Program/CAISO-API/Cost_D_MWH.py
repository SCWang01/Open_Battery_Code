"""Calculate natural-gas consumption per MWh for monthly CAISO workbooks.

For every ``CAISO_NG_YYYY_MM.xlsx`` file, this script adds or updates:

    Mcf_per_MWh = 1 / (MMBtuPer_Unit * Netgen / Elec_MMBtu)
    average_capacity = Netgen / calendar hours in YEAR/MONTH

Rows with missing/non-numeric/zero calculation inputs, or with ``Netgen <= 0``,
are removed before output.  By default, workbooks in ``Month_Agg_Clear`` are
updated in place.
"""

from __future__ import annotations

import argparse
import calendar
import os
import tempfile
from pathlib import Path

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT_DIR = BASE_DIR / "Month_Agg_Clear"
DEFAULT_PATTERN = "CAISO_NG_????_??.xlsx"

HEAT_CONTENT_COLUMN = "MMBtuPer_Unit"
ELECTRIC_FUEL_COLUMN = "Elec_MMBtu"
NET_GENERATION_COLUMN = "Netgen"
YEAR_COLUMN = "YEAR"
MONTH_COLUMN = "MONTH"
OUTPUT_COLUMN = "Mcf_per_MWh"
AVERAGE_CAPACITY_COLUMN = "average_capacity"
REQUIRED_COLUMNS = (
    HEAT_CONTENT_COLUMN,
    ELECTRIC_FUEL_COLUMN,
    NET_GENERATION_COLUMN,
)


def calculate_mcf_per_mwh(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Return calculated values and a mask identifying rows to remove."""
    missing_columns = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing_columns:
        raise KeyError(
            f"Missing columns required for calculation: {', '.join(missing_columns)}"
        )

    heat_content = pd.to_numeric(frame[HEAT_CONTENT_COLUMN], errors="coerce")
    electric_mmbtu = pd.to_numeric(frame[ELECTRIC_FUEL_COLUMN], errors="coerce")
    net_generation = pd.to_numeric(frame[NET_GENERATION_COLUMN], errors="coerce")

    # Elec_MMBtu = 0 would make Netgen / Elec_MMBtu invalid. Missing values and
    # zero heat content also make the reciprocal undefined. Netgen <= 0 is not
    # a usable positive-generation observation and is explicitly removed.
    valid = (
        heat_content.notna()
        & electric_mmbtu.notna()
        & net_generation.notna()
        & heat_content.ne(0)
        & electric_mmbtu.ne(0)
        & net_generation.gt(0)
    )

    result = pd.Series(float("nan"), index=frame.index, dtype="float64")
    denominator = heat_content.loc[valid] * (
        net_generation.loc[valid] / electric_mmbtu.loc[valid]
    )
    result.loc[valid] = 1.0 / denominator
    return result, ~valid


def calculate_average_capacity(frame: pd.DataFrame) -> pd.Series:
    """Calculate average capacity using the actual hours in YEAR/MONTH.

    A monthly workbook must contain exactly one valid integer year/month pair.
    Missing, fractional, out-of-range, or mixed period values fail fast so an
    incorrect denominator cannot silently enter the output.
    """
    required_columns = (NET_GENERATION_COLUMN, YEAR_COLUMN, MONTH_COLUMN)
    missing_columns = [
        column for column in required_columns if column not in frame.columns
    ]
    if missing_columns:
        raise KeyError(
            f"Missing columns required for calculation: {', '.join(missing_columns)}"
        )

    year = pd.to_numeric(frame[YEAR_COLUMN], errors="coerce")
    month = pd.to_numeric(frame[MONTH_COLUMN], errors="coerce")

    invalid_year = (
        year.isna()
        | year.lt(1)
        | year.gt(9999)
        | year.mod(1).ne(0)
    )
    invalid_month = (
        month.isna()
        | month.lt(1)
        | month.gt(12)
        | month.mod(1).ne(0)
    )
    if invalid_year.any() or invalid_month.any():
        invalid_rows = frame.index[invalid_year | invalid_month].tolist()
        preview = invalid_rows[:10]
        suffix = "..." if len(invalid_rows) > len(preview) else ""
        raise ValueError(
            "YEAR and MONTH must be valid integers, and MONTH must be in "
            f"the range 1-12; invalid row indices: {preview}{suffix}"
        )

    periods = pd.DataFrame(
        {
            YEAR_COLUMN: year.astype("int64"),
            MONTH_COLUMN: month.astype("int64"),
        }
    ).drop_duplicates()
    if len(periods) != 1:
        detected = [
            (int(row[YEAR_COLUMN]), int(row[MONTH_COLUMN]))
            for _, row in periods.iterrows()
        ]
        raise ValueError(
            f"Multiple YEAR/MONTH pairs detected in one monthly file: {detected}"
        )

    data_year = int(periods.iloc[0][YEAR_COLUMN])
    data_month = int(periods.iloc[0][MONTH_COLUMN])
    days_in_month = calendar.monthrange(data_year, data_month)[1]
    hours_in_month = float(days_in_month * 24)

    net_generation = pd.to_numeric(frame[NET_GENERATION_COLUMN], errors="coerce")
    return net_generation / hours_in_month


def write_excel_atomically(frame: pd.DataFrame, output_file: Path) -> None:
    """Write an XLSX file, replacing the destination only after a successful save."""
    output_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None

    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{output_file.stem}_",
            suffix=".xlsx",
            dir=output_file.parent,
            delete=False,
        ) as temporary_file:
            temporary_name = temporary_file.name

        frame.to_excel(temporary_name, index=False, engine="openpyxl")
        os.replace(temporary_name, output_file)
    except Exception:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
        raise


def process_workbook(input_file: Path, output_file: Path) -> tuple[int, int]:
    """Remove invalid rows, add calculated columns, and return row counts."""
    frame = pd.read_excel(input_file, engine="openpyxl")
    mcf_per_mwh, rows_to_remove = calculate_mcf_per_mwh(frame)
    average_capacity = calculate_average_capacity(frame)

    cleaned = frame.loc[~rows_to_remove].copy()
    cleaned[OUTPUT_COLUMN] = mcf_per_mwh.loc[~rows_to_remove]
    cleaned[AVERAGE_CAPACITY_COLUMN] = average_capacity.loc[~rows_to_remove]
    cleaned = cleaned.reset_index(drop=True)

    write_excel_atomically(cleaned, output_file)
    return len(frame), int(rows_to_remove.sum())


def process_monthly_files(
    input_dir: Path,
    output_dir: Path | None = None,
    pattern: str = DEFAULT_PATTERN,
) -> list[tuple[str, int, int]]:
    """Process all matching monthly files, in place unless output_dir is given."""
    input_dir = input_dir.resolve()
    if not input_dir.is_dir():
        raise FileNotFoundError(f"Input directory not found: {input_dir}")

    input_files = sorted(input_dir.glob(pattern))
    if not input_files:
        raise FileNotFoundError(
            f"No files matching {pattern} found in the input directory: {input_dir}"
        )

    resolved_output_dir = output_dir.resolve() if output_dir is not None else input_dir
    results: list[tuple[str, int, int]] = []

    for input_file in input_files:
        output_file = resolved_output_dir / input_file.name
        row_count, removed_count = process_workbook(input_file, output_file)
        results.append((input_file.name, row_count, removed_count))

    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Add an Mcf_per_MWh column to monthly CAISO gas workbooks."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help="Input directory (default: Month_Agg_Clear).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Optional output directory; update input files in place if omitted.",
    )
    parser.add_argument(
        "--pattern",
        default=DEFAULT_PATTERN,
        help=f"File-matching pattern (default: {DEFAULT_PATTERN}).",
    )
    return parser.parse_args()


def main() -> None:
    arguments = parse_args()
    results = process_monthly_files(
        input_dir=arguments.input_dir,
        output_dir=arguments.output_dir,
        pattern=arguments.pattern,
    )

    destination = arguments.output_dir or arguments.input_dir
    print(f"Processed {len(results)} files; output: {destination.resolve()}")
    for file_name, row_count, removed_count in results:
        print(
            f"{file_name}: {row_count} -> {row_count - removed_count} rows; "
            f"removed {removed_count} rows"
        )


if __name__ == "__main__":
    main()
