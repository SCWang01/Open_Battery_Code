"""Remove monthly rows whose MMBtuPer_Unit is zero or a dot placeholder."""

import argparse
from pathlib import Path

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT_DIR = BASE_DIR / "Month_Agg"
DEFAULT_OUTPUT_DIR = BASE_DIR / "Month_Agg_Clear"
TARGET_COLUMN = "MMBtuPer_Unit"


def rows_to_remove(series: pd.Series) -> pd.Series:
    """Identify numeric/string zero and the EIA dot placeholder."""
    text = series.astype("string").str.strip()
    numeric = pd.to_numeric(text, errors="coerce")
    return text.eq(".").fillna(False) | numeric.eq(0).fillna(False)


def clear_workbooks(input_dir: Path, output_dir: Path) -> list[tuple[str, int, int]]:
    input_dir = input_dir.resolve()
    output_dir = output_dir.resolve()
    if not input_dir.is_dir():
        raise FileNotFoundError(f"Input directory not found: {input_dir}")
    if input_dir == output_dir:
        raise ValueError("Input and output directories must be different")

    input_files = sorted(input_dir.glob("CAISO_NG_*.xlsx"))
    if not input_files:
        raise FileNotFoundError(
            f"No CAISO_NG_*.xlsx files found in the input directory: {input_dir}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[tuple[str, int, int]] = []

    for input_file in input_files:
        frame = pd.read_excel(input_file)
        if TARGET_COLUMN not in frame.columns:
            raise KeyError(f"{input_file.name} is missing column: {TARGET_COLUMN}")

        remove = rows_to_remove(frame[TARGET_COLUMN])
        cleaned = frame.loc[~remove].copy()
        output_file = output_dir / input_file.name
        cleaned.to_excel(output_file, index=False, engine="openpyxl")
        results.append((input_file.name, len(frame), len(cleaned)))

    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Remove monthly rows where MMBtuPer_Unit is zero or the EIA dot "
            "placeholder."
        )
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help="Directory containing the original monthly files.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for cleaned monthly files.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    summary = clear_workbooks(arguments.input_dir, arguments.output_dir)
    print(
        f"Created {len(summary)} cleaned files in: "
        f"{arguments.output_dir.resolve()}"
    )
    for name, before, after in summary:
        print(f"{name}: {before} -> {after}; removed {before - after}")
    print(
        f"Total: {sum(item[1] for item in summary)} -> "
        f"{sum(item[2] for item in summary)}"
    )
