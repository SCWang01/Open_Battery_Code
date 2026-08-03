"""Add generator capacity and minimum-load values to monthly CAISO files.

The default inputs are:

* ``Month_Agg_Clear/CAISO_NG_YYYY_MM.xlsx``
* ``Month_Agg_Clear/CAISO_NG_Plant_Capacity_Minimum_Load.xlsx``

The monthly files are updated in place with ``capacity`` and
``Minimum Load (MW)``.  Capacity is the smallest leading cumulative sum of
the corresponding ``Nameplate Capacity (MW)`` array that reaches monthly
``average_capacity``, plus one additional following generator as reserve when
available.  Minimum load uses ``a[0]``.  Unmatched, empty, malformed, or null
values are written as blank cells without removing the monthly row.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Hashable

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_MONTHLY_DIR = BASE_DIR / "Month_Agg_Clear"
DEFAULT_CAPACITY_FILE = (
    DEFAULT_MONTHLY_DIR / "CAISO_NG_Plant_Capacity_Minimum_Load_2023_01_to_2026_04.xlsx"
)
DEFAULT_PATTERN = "CAISO_NG_????_??.xlsx"

MONTHLY_PLANT_COLUMN = "Plant Id"
MONTHLY_MOVER_COLUMN = "Reported Prime Mover"
AVERAGE_CAPACITY_COLUMN = "average_capacity"
SOURCE_PLANT_COLUMN = "Plant ID"
SOURCE_MOVER_COLUMN = "Prime Mover"
SOURCE_CAPACITY_COLUMN = "Nameplate Capacity (MW)"
SOURCE_MINIMUM_COLUMN = "Minimum Load (MW)"
OUTPUT_CAPACITY_COLUMN = "capacity"
LEGACY_CAPACITY_COLUMN = "capacitiy"
OUTPUT_MINIMUM_COLUMN = "Minimum Load (MW)"

PlantId = Hashable
MatchKey = tuple[PlantId, str]
MatchedValues = tuple[tuple[float, ...] | None, object | None]


def normalize_plant_id(value: object) -> PlantId | None:
    """Normalize numeric-looking IDs so 246, 246.0, and '246' match."""
    if value is None or isinstance(value, bool):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    text = str(value).strip()
    if not text:
        return None

    try:
        number = float(text)
    except ValueError:
        return text.casefold()
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def normalize_prime_mover(value: object) -> str:
    """Normalize prime-mover codes for case- and whitespace-insensitive matching."""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip().casefold()


def parse_array(value: object) -> list[object] | tuple[object, ...] | None:
    """Parse a JSON-style array, returning None for invalid input."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    parsed: object
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return None
    else:
        parsed = value

    if not isinstance(parsed, (list, tuple)):
        return None
    return parsed


def valid_array_item(value: object) -> object | None:
    """Return one usable array item, treating null/NaN as missing."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def first_array_value(value: object) -> object | None:
    """Return a[0], or None for an invalid, empty, or null array."""
    parsed = parse_array(value)
    if not parsed:
        return None
    return valid_array_item(parsed[0])


def capacity_array_values(value: object) -> tuple[float, ...] | None:
    """Return every numeric capacity item, or None if any item is invalid."""
    parsed = parse_array(value)
    if not parsed:
        return None

    values: list[float] = []
    for item in parsed:
        item = valid_array_item(item)
        if item is None:
            return None
        try:
            numeric_item = float(item)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(numeric_item):
            return None
        values.append(numeric_item)
    return tuple(values)


def cumulative_capacity_for_average(
    capacity_values: tuple[float, ...] | None,
    average_capacity: object,
) -> float | None:
    """Reach the average, then include one following generator as reserve."""
    if not capacity_values:
        return None

    numeric_average = pd.to_numeric(average_capacity, errors="coerce")
    if pd.isna(numeric_average) or not math.isfinite(float(numeric_average)):
        return None

    cumulative_capacity = 0.0
    for index, capacity in enumerate(capacity_values):
        cumulative_capacity += capacity
        if cumulative_capacity >= float(numeric_average):
            next_index = index + 1
            if next_index < len(capacity_values):
                cumulative_capacity += capacity_values[next_index]
            break
    return cumulative_capacity


def load_capacity_lookups(
    capacity_file: Path,
) -> tuple[dict[MatchKey, MatchedValues], dict[PlantId, MatchedValues]]:
    """Build an exact plant/mover lookup and a unique-plant fallback lookup."""
    if not capacity_file.is_file():
        raise FileNotFoundError(f"Capacity file not found: {capacity_file}")

    frame = pd.read_excel(capacity_file, engine="openpyxl")
    required_columns = (
        SOURCE_PLANT_COLUMN,
        SOURCE_MOVER_COLUMN,
        SOURCE_CAPACITY_COLUMN,
        SOURCE_MINIMUM_COLUMN,
    )
    missing = [column for column in required_columns if column not in frame.columns]
    if missing:
        raise KeyError(f"Capacity file is missing columns: {', '.join(missing)}")

    prepared: list[tuple[PlantId, str, MatchedValues]] = []
    for _, row in frame.iterrows():
        plant_id = normalize_plant_id(row[SOURCE_PLANT_COLUMN])
        prime_mover = normalize_prime_mover(row[SOURCE_MOVER_COLUMN])
        if plant_id is None:
            continue
        values = (
            capacity_array_values(row[SOURCE_CAPACITY_COLUMN]),
            first_array_value(row[SOURCE_MINIMUM_COLUMN]),
        )
        prepared.append((plant_id, prime_mover, values))

    plant_counts = Counter(plant_id for plant_id, _, _ in prepared)
    exact_lookup: dict[MatchKey, MatchedValues] = {}
    unique_plant_lookup: dict[PlantId, MatchedValues] = {}

    for plant_id, prime_mover, values in prepared:
        if prime_mover:
            exact_lookup.setdefault((plant_id, prime_mover), values)
        if plant_counts[plant_id] == 1:
            unique_plant_lookup[plant_id] = values

    return exact_lookup, unique_plant_lookup


def match_monthly_frame(
    frame: pd.DataFrame,
    exact_lookup: dict[MatchKey, MatchedValues],
    unique_plant_lookup: dict[PlantId, MatchedValues],
) -> tuple[pd.DataFrame, int, int]:
    """Add matched columns and return match and incomplete-row counts."""
    required_monthly_columns = (MONTHLY_PLANT_COLUMN, AVERAGE_CAPACITY_COLUMN)
    missing = [
        column for column in required_monthly_columns if column not in frame.columns
    ]
    if missing:
        raise KeyError(f"Monthly file is missing columns: {', '.join(missing)}")

    has_mover = MONTHLY_MOVER_COLUMN in frame.columns
    capacities: list[object | None] = []
    minimum_loads: list[object | None] = []
    matched_rows = 0
    incomplete_rows = 0

    for _, row in frame.iterrows():
        plant_id = normalize_plant_id(row[MONTHLY_PLANT_COLUMN])
        prime_mover = (
            normalize_prime_mover(row[MONTHLY_MOVER_COLUMN]) if has_mover else ""
        )

        values = exact_lookup.get((plant_id, prime_mover)) if plant_id is not None else None
        if values is None and plant_id is not None:
            values = unique_plant_lookup.get(plant_id)

        if values is None:
            capacity, minimum_load = None, None
        else:
            capacity_values, minimum_load = values
            capacity = cumulative_capacity_for_average(
                capacity_values, row[AVERAGE_CAPACITY_COLUMN]
            )
            matched_rows += 1

        if capacity is None or minimum_load is None:
            incomplete_rows += 1
        capacities.append(capacity)
        minimum_loads.append(minimum_load)

    old_calculated_columns = [
        column
        for column in (
            LEGACY_CAPACITY_COLUMN,
            OUTPUT_CAPACITY_COLUMN,
            OUTPUT_MINIMUM_COLUMN,
        )
        if column in frame.columns
    ]
    result = frame.drop(columns=old_calculated_columns).copy()
    result[OUTPUT_CAPACITY_COLUMN] = capacities
    result[OUTPUT_MINIMUM_COLUMN] = minimum_loads
    return result, matched_rows, incomplete_rows


def write_excel_atomically(frame: pd.DataFrame, output_file: Path) -> None:
    """Replace an XLSX file only after the updated workbook is saved successfully."""
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


def process_monthly_files(
    monthly_dir: Path,
    capacity_file: Path,
    output_dir: Path | None = None,
    pattern: str = DEFAULT_PATTERN,
) -> list[tuple[str, int, int, int]]:
    """Match all monthly workbooks, updating them in place by default."""
    monthly_dir = monthly_dir.resolve()
    if not monthly_dir.is_dir():
        raise FileNotFoundError(f"Monthly-file directory not found: {monthly_dir}")

    monthly_files = sorted(monthly_dir.glob(pattern))
    if not monthly_files:
        raise FileNotFoundError(
            f"No monthly files matching {pattern} found in: {monthly_dir}"
        )

    exact_lookup, unique_plant_lookup = load_capacity_lookups(capacity_file.resolve())
    destination = output_dir.resolve() if output_dir is not None else monthly_dir
    results: list[tuple[str, int, int, int]] = []

    for input_file in monthly_files:
        frame = pd.read_excel(input_file, engine="openpyxl")
        matched, matched_rows, incomplete_rows = match_monthly_frame(
            frame, exact_lookup, unique_plant_lookup
        )
        write_excel_atomically(matched, destination / input_file.name)
        results.append(
            (input_file.name, len(frame), matched_rows, incomplete_rows)
        )

    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Match generator capacity and the first minimum-load array value "
            "to monthly CAISO files."
        )
    )
    parser.add_argument(
        "--monthly-dir",
        type=Path,
        default=DEFAULT_MONTHLY_DIR,
        help="Directory containing CAISO_NG_YYYY_MM.xlsx files.",
    )
    parser.add_argument(
        "--capacity-file",
        type=Path,
        default=DEFAULT_CAPACITY_FILE,
        help="Generator-capacity and minimum-load lookup workbook.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Optional output directory; update monthly files in place if omitted.",
    )
    parser.add_argument(
        "--pattern",
        default=DEFAULT_PATTERN,
        help=f"Monthly file-matching pattern (default: {DEFAULT_PATTERN}).",
    )
    return parser.parse_args()


def main() -> None:
    arguments = parse_args()
    results = process_monthly_files(
        monthly_dir=arguments.monthly_dir,
        capacity_file=arguments.capacity_file,
        output_dir=arguments.output_dir,
        pattern=arguments.pattern,
    )

    destination = arguments.output_dir or arguments.monthly_dir
    print(f"Processed {len(results)} files; output: {destination.resolve()}")
    for file_name, row_count, matched_rows, incomplete_rows in results:
        print(
            f"{file_name}: {row_count} rows; matched {matched_rows}; "
            f"unmatched or incomplete {incomplete_rows}"
        )


if __name__ == "__main__":
    main()
