"""Calculate and rank monthly CAISO natural-gas generation costs.

By default, the script processes every month from 2023-01 through 2026-04. It
reads California monthly natural-gas prices sold to electric-power consumers
from ``Month_Agg_Clear/Fuel_Cost.xls`` and multiplies each month's price by
``Mcf_per_MWh`` in ``CAISO_NG_YYYY_MM.xlsx``:

    $_per_mwh = monthly fuel cost ($/Mcf) * Mcf_per_MWh

Rows are sorted from lowest to highest ``$_per_mwh`` and written as
``CAISO_NG_Final_YYYY_MM.xlsx`` in ``Month_Agg_Clear_V2``.  Rows with a blank
or negative ``$_per_mwh`` are removed before sorting and writing.
"""

from __future__ import annotations

import argparse
import os
import re
import tempfile
from pathlib import Path

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT_DIR = BASE_DIR / "Month_Agg_Clear"
DEFAULT_FUEL_COST_FILE = DEFAULT_INPUT_DIR / "Fuel_Cost.xls"
DEFAULT_OUTPUT_DIR = BASE_DIR / "Month_Agg_Clear_V2"
DEFAULT_START = (2023, 1)
DEFAULT_END = (2026, 4)
FUEL_COST_SHEET = "Data 1"

MCF_PER_MWH_COLUMN = "Mcf_per_MWh"
OUTPUT_COST_COLUMN = "$_per_mwh"
MONTHLY_FILE_PATTERN = re.compile(r"^CAISO_NG_(\d{4})_(\d{2})\.xlsx$")
YEAR_MONTH_PATTERN = re.compile(r"^(\d{4})-(\d{2})$")

YearMonth = tuple[int, int]
ProcessResult = tuple[str, int, int, int, int, float]


def parse_year_month(value: str) -> YearMonth:
    """Parse one YYYY-MM command-line value."""
    match = YEAR_MONTH_PATTERN.fullmatch(value.strip())
    if match is None:
        raise argparse.ArgumentTypeError(
            f"Year and month must use YYYY-MM format: {value}"
        )

    year, month = (int(part) for part in match.groups())
    if year < 1 or year > 9999 or month < 1 or month > 12:
        raise argparse.ArgumentTypeError(f"Invalid year and month: {value}")
    return year, month


def iter_year_months(start: YearMonth, end: YearMonth):
    """Yield inclusive year/month pairs from start through end."""
    if start > end:
        raise ValueError(
            f"The start month cannot be later than the end month: "
            f"{start[0]}-{start[1]:02d} > "
            f"{end[0]}-{end[1]:02d}"
        )

    year, month = start
    while (year, month) <= end:
        yield year, month
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1


def read_monthly_fuel_costs(fuel_cost_file: Path) -> dict[tuple[int, int], float]:
    """Read valid monthly $/Mcf values from the EIA-style legacy XLS file."""
    if not fuel_cost_file.is_file():
        raise FileNotFoundError(f"Fuel-cost file not found: {fuel_cost_file}")

    try:
        raw = pd.read_excel(
            fuel_cost_file,
            sheet_name=FUEL_COST_SHEET,
            header=None,
            skiprows=3,
            usecols=[0, 1],
            names=["Date", "Fuel_Cost"],
            engine="xlrd",
        )
    except ImportError as error:
        raise ImportError(
            "Reading .xls files requires xlrd; run: python -m pip install xlrd"
        ) from error

    raw["Date"] = pd.to_datetime(raw["Date"], errors="coerce")
    raw["Fuel_Cost"] = pd.to_numeric(raw["Fuel_Cost"], errors="coerce")
    valid = raw.loc[raw["Date"].notna() & raw["Fuel_Cost"].notna()].copy()

    monthly_costs: dict[tuple[int, int], float] = {}
    for _, row in valid.iterrows():
        key = (int(row["Date"].year), int(row["Date"].month))
        if key in monthly_costs:
            raise ValueError(
                f"Duplicate month in fuel-cost file: {key[0]}-{key[1]:02d}"
            )
        monthly_costs[key] = float(row["Fuel_Cost"])
    return monthly_costs


def find_cost_input_column(frame: pd.DataFrame) -> str:
    """Find Mcf_per_MWh without depending on letter case."""
    target = MCF_PER_MWH_COLUMN.casefold()
    matches = [column for column in frame.columns if str(column).casefold() == target]
    if not matches:
        raise KeyError(f"Monthly file is missing column: {MCF_PER_MWH_COLUMN}")
    if len(matches) > 1:
        raise KeyError(
            f"Monthly file contains multiple {MCF_PER_MWH_COLUMN} columns"
        )
    return str(matches[0])


def calculate_clean_and_sort(
    frame: pd.DataFrame, fuel_cost: float
) -> tuple[pd.DataFrame, int, int]:
    """Calculate cost, remove blank/negative rows, then sort ascending."""
    input_column = find_cost_input_column(frame)
    mcf_per_mwh = pd.to_numeric(frame[input_column], errors="coerce")

    result = frame.copy()
    result[OUTPUT_COST_COLUMN] = mcf_per_mwh * fuel_cost
    blank_cost = result[OUTPUT_COST_COLUMN].isna()
    negative_cost = result[OUTPUT_COST_COLUMN].lt(0)
    cleaned = result.loc[~blank_cost & ~negative_cost].copy()
    cleaned = cleaned.sort_values(
        OUTPUT_COST_COLUMN,
        ascending=True,
        kind="mergesort",
    )
    return (
        cleaned.reset_index(drop=True),
        int(blank_cost.sum()),
        int(negative_cost.sum()),
    )


def write_excel_atomically(frame: pd.DataFrame, output_file: Path) -> None:
    """Replace an output only after the new XLSX file saves successfully."""
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


def validate_monthly_identity(frame: pd.DataFrame, year: int, month: int) -> None:
    """Check YEAR/MONTH fields when present so files cannot be mislabeled."""
    for column, expected in (("YEAR", year), ("MONTH", month)):
        if column not in frame.columns:
            continue
        values = pd.to_numeric(frame[column], errors="coerce").dropna().unique()
        if len(values) != 1 or int(values[0]) != expected:
            raise ValueError(
                f"The filename month conflicts with the {column} column; "
                f"expected {expected}, found {values.tolist()}"
            )


def process_range(
    start: YearMonth,
    end: YearMonth,
    input_dir: Path,
    fuel_cost_file: Path,
    output_dir: Path,
) -> list[ProcessResult]:
    """Create ranked monthly workbooks for an inclusive month range."""
    input_dir = input_dir.resolve()
    fuel_cost_file = fuel_cost_file.resolve()
    output_dir = output_dir.resolve()

    if not input_dir.is_dir():
        raise FileNotFoundError(f"Monthly input directory not found: {input_dir}")

    requested_months = list(iter_year_months(start, end))
    monthly_costs = read_monthly_fuel_costs(fuel_cost_file)
    missing_prices = [period for period in requested_months if period not in monthly_costs]
    if missing_prices:
        formatted = ", ".join(
            f"{year}-{month:02d}" for year, month in missing_prices
        )
        raise ValueError(
            f"Fuel_Cost.xls has no valid price for these months: {formatted}"
        )

    missing_files = [
        input_dir / f"CAISO_NG_{year}_{month:02d}.xlsx"
        for year, month in requested_months
        if not (input_dir / f"CAISO_NG_{year}_{month:02d}.xlsx").is_file()
    ]
    if missing_files:
        raise FileNotFoundError(
            "Missing monthly files: " + ", ".join(path.name for path in missing_files)
        )

    results: list[ProcessResult] = []
    for year, month in requested_months:
        input_file = input_dir / f"CAISO_NG_{year}_{month:02d}.xlsx"
        output_file = output_dir / f"CAISO_NG_Final_{year}_{month:02d}.xlsx"
        fuel_cost = monthly_costs[(year, month)]

        frame = pd.read_excel(input_file, engine="openpyxl")
        validate_monthly_identity(frame, year, month)
        final, blank_count, negative_count = calculate_clean_and_sort(
            frame, fuel_cost
        )
        write_excel_atomically(final, output_file)
        results.append(
            (
                output_file.name,
                len(frame),
                len(final),
                blank_count,
                negative_count,
                fuel_cost,
            )
        )

    return results


def process_year(
    year: int,
    input_dir: Path,
    fuel_cost_file: Path,
    output_dir: Path,
) -> list[ProcessResult]:
    """Keep the original full-year Python API for compatibility."""
    return process_range(
        start=(year, 1),
        end=(year, 12),
        input_dir=input_dir,
        fuel_cost_file=fuel_cost_file,
        output_dir=output_dir,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Calculate monthly natural-gas generation cost in $/MWh and sort "
            "by ascending cost. The default period is 2023-01 through 2026-04."
        )
    )
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        help=(
            "Compatibility mode: process January-December of one year; cannot "
            "be combined with --start/--end."
        ),
    )
    parser.add_argument(
        "--start",
        type=parse_year_month,
        default=None,
        help="First month in YYYY-MM format; must be used with --end.",
    )
    parser.add_argument(
        "--end",
        type=parse_year_month,
        default=None,
        help="Last month in YYYY-MM format; must be used with --start.",
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help="Directory containing CAISO_NG_YYYY_MM.xlsx files.",
    )
    parser.add_argument(
        "--fuel-cost-file",
        type=Path,
        default=DEFAULT_FUEL_COST_FILE,
        help="Path to Fuel_Cost.xls.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for final monthly output files.",
    )
    return parser.parse_args()


def main() -> None:
    arguments = parse_args()
    if arguments.year is not None and (
        arguments.start is not None or arguments.end is not None
    ):
        raise SystemExit("--year cannot be combined with --start/--end")
    if (arguments.start is None) != (arguments.end is None):
        raise SystemExit("--start and --end must be used together")

    if arguments.year is not None:
        start = (arguments.year, 1)
        end = (arguments.year, 12)
    elif arguments.start is not None and arguments.end is not None:
        start = arguments.start
        end = arguments.end
    else:
        start = DEFAULT_START
        end = DEFAULT_END

    results = process_range(
        start=start,
        end=end,
        input_dir=arguments.input_dir,
        fuel_cost_file=arguments.fuel_cost_file,
        output_dir=arguments.output_dir,
    )

    print(
        f"Created {len(results)} files for {start[0]}-{start[1]:02d} through "
        f"{end[0]}-{end[1]:02d} in: {arguments.output_dir.resolve()}"
    )
    for (
        file_name,
        original_count,
        final_count,
        blank_count,
        negative_count,
        fuel_cost,
    ) in results:
        print(
            f"{file_name}: fuel price ${fuel_cost:.4f}/Mcf; "
            f"{original_count} -> {final_count} rows; removed {blank_count} "
            f"blank rows and {negative_count} negative rows"
        )


if __name__ == "__main__":
    main()
