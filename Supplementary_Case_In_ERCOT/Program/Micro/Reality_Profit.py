"""Export monthly hourly telemetry power and realized profit.

Read five-minute Usage sheets using experiment_config_1hour.json and export
Realized_Profit_<member>_1hour_YYYYMM.xlsx under Results/Micro_Results/realized_profit.
Profit sums paired five-minute price * power / 12, filling missing slots with
the same hour's mean paired profit. Hours without valid pairs remain blank.
Telemetered Net Output is the hourly mean of available power samples in MW;
LMP is independently averaged and is not multiplied by that mean for profit.
UTC identifies physical hours; local month boundaries use America/Chicago.
No status filter, SOC model, efficiency adjustment, or optimization is applied.
"""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

import Experiment_Data_1hour as experiment_data


# ---------------------------------------------------------------------------
# Contract and schema
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_EXPERIMENT_PATH = BASE_DIR / "experiment_config_1hour.json"
DEFAULT_OUTPUT_SUBDIRECTORY = "realized_profit"
INTERVALS_PER_HOUR = 12
TIME_FREQUENCY = "5min"
TIMEZONE = "America/Chicago"

STANDARD_TIME_COLUMN = experiment_data.STANDARD_TIME
SOURCE_TIME_COLUMN = experiment_data.SOURCE_TIME
SCED_TIME_COLUMN = experiment_data.SCED_TIME
UTC_SOURCE_COLUMN = "SCED Timestamp UTC"
UTC_HOUR_COLUMN = "Hour Start UTC"
UTC_SLOT_COLUMN = "_UTC Slot"
RESOURCE_COLUMN = "Resource Name"
STATUS_COLUMN = experiment_data.STATUS
LMP_COLUMN = "LMP"
TELEMETRY_POWER_COLUMN = "Telemetered Net Output"
PROFIT_COLUMN = "Profit"
OBSERVED_PROFIT_COLUMN = "Observed Profit"
PROFIT_SAMPLE_COUNT_COLUMN = "Profit Sample Count"
IMPUTED_SAMPLE_COUNT_COLUMN = "Imputed Sample Count"
PROFIT_COVERAGE_COLUMN = "Profit Coverage"

REQUIRED_INPUT_COLUMNS = (
    SOURCE_TIME_COLUMN,
    SCED_TIME_COLUMN,
    UTC_SOURCE_COLUMN,
    RESOURCE_COLUMN,
    STATUS_COLUMN,
    LMP_COLUMN,
    TELEMETRY_POWER_COLUMN,
)
OUTPUT_COLUMNS = (
    STANDARD_TIME_COLUMN,
    UTC_HOUR_COLUMN,
    "Month",
    RESOURCE_COLUMN,
    STATUS_COLUMN,
    LMP_COLUMN,
    TELEMETRY_POWER_COLUMN,
    OBSERVED_PROFIT_COLUMN,
    PROFIT_SAMPLE_COUNT_COLUMN,
    IMPUTED_SAMPLE_COUNT_COLUMN,
    PROFIT_COVERAGE_COLUMN,
    PROFIT_COLUMN,
)


# ---------------------------------------------------------------------------
# Boundary validation and input loading
# ---------------------------------------------------------------------------

def _find_member_input(data_dir: Path, member: str, month: str) -> Path:
    """Find the configured member's monthly five-minute workbook."""

    path = data_dir / f"{member}_Usage_{month.replace('-', '')}.xlsx"
    if not path.is_file():
        raise FileNotFoundError(
            f"missing input for selected member {member!r}, month {month}: {path}"
        )
    return path


def _read_input(path: Path) -> pd.DataFrame:
    """Read one member input while preserving source status text."""

    return pd.read_excel(
        path,
        sheet_name="Usage",
        keep_default_na=False,
        usecols=lambda column: column in REQUIRED_INPUT_COLUMNS,
    )


def _validate_columns(frame: pd.DataFrame, source_path: Path) -> None:
    """Reject an input that cannot support the realized-profit contract."""

    missing = sorted(set(REQUIRED_INPUT_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(f"{source_path}: missing required columns: {missing}")


def _select_study_rows(
    frame: pd.DataFrame,
    source_path: Path,
    member: str,
    experiment: experiment_data.Experiment,
) -> pd.DataFrame:
    """Validate source slots and select the local month without forecast tails."""

    selected = frame.copy()
    try:
        for column in (SOURCE_TIME_COLUMN, SCED_TIME_COLUMN):
            selected[column] = pd.to_datetime(
                selected[column], errors="raise"
            ).dt.round("s")
        selected[UTC_SLOT_COLUMN] = (
            pd.to_datetime(selected[UTC_SOURCE_COLUMN], errors="raise", utc=True)
            .dt.round("s")
            .dt.floor(TIME_FREQUENCY)
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{source_path}: invalid timestamp format") from exc

    if selected[[SOURCE_TIME_COLUMN, SCED_TIME_COLUMN, UTC_SLOT_COLUMN]].isna().any().any():
        raise ValueError(f"{source_path}: missing timestamp")
    local_slots = selected[UTC_SLOT_COLUMN].dt.tz_convert(TIMEZONE).dt.tz_localize(None)
    if not local_slots.equals(selected[SOURCE_TIME_COLUMN]):
        raise ValueError(
            f"{source_path}: UTC slots disagree with local standard timestamps"
        )
    if selected[UTC_SLOT_COLUMN].duplicated().any():
        duplicate = selected.loc[
            selected[UTC_SLOT_COLUMN].duplicated(), UTC_SLOT_COLUMN
        ].iloc[0]
        raise ValueError(f"{source_path}: duplicate five-minute UTC slot at {duplicate}")

    names = selected[RESOURCE_COLUMN].astype(str).str.strip()
    if not names.eq(member).all():
        raise ValueError(
            f"{source_path}: Resource Name does not consistently match {member!r}"
        )
    selected[RESOURCE_COLUMN] = names
    selected = selected.loc[
        selected[UTC_SLOT_COLUMN].ge(experiment.start.tz_convert("UTC"))
        & selected[UTC_SLOT_COLUMN].lt(experiment.end_exclusive.tz_convert("UTC"))
    ].copy()
    return selected.sort_values(UTC_SLOT_COLUMN, kind="stable").reset_index(drop=True)


def _validate_numeric_inputs(frame: pd.DataFrame, source_path: Path) -> pd.DataFrame:
    """Keep blanks missing, but reject nonblank invalid prices and power."""

    normalized = frame.copy()
    for column in (LMP_COLUMN, TELEMETRY_POWER_COLUMN):
        original = normalized[column]
        blank = original.isna() | original.astype(str).str.strip().eq("")
        numeric = pd.to_numeric(original.where(~blank, np.nan), errors="coerce")
        invalid = ~np.isfinite(numeric.to_numpy(dtype=float)) | original.map(
            lambda value: isinstance(value, (bool, np.bool_))
        )
        if (invalid & ~blank).any():
            raise ValueError(f"{source_path}: invalid nonblank {column}")
        normalized[column] = numeric
    return normalized


# ---------------------------------------------------------------------------
# Derivation and output assembly
# ---------------------------------------------------------------------------

def _build_output(
    frame: pd.DataFrame, member: str, experiment: experiment_data.Experiment
) -> pd.DataFrame:
    """Aggregate paired profits and export independent hourly mean power in MW."""

    source = frame.copy()
    source[UTC_HOUR_COLUMN] = source[UTC_SLOT_COLUMN].dt.floor("h")
    source[OBSERVED_PROFIT_COLUMN] = (
        source[LMP_COLUMN] * source[TELEMETRY_POWER_COLUMN] / INTERVALS_PER_HOUR
    )
    source[STATUS_COLUMN] = source[STATUS_COLUMN].fillna("").astype(str).str.strip().str.upper()
    grouped = source.groupby(UTC_HOUR_COLUMN, sort=True)
    hours = pd.date_range(
        experiment.start.tz_convert("UTC"),
        experiment.end_exclusive.tz_convert("UTC"),
        freq="h",
        inclusive="left",
    )
    output = grouped[[LMP_COLUMN, TELEMETRY_POWER_COLUMN]].mean().reindex(hours)
    output[OBSERVED_PROFIT_COLUMN] = grouped[OBSERVED_PROFIT_COLUMN].sum(min_count=1)
    counts = grouped[OBSERVED_PROFIT_COLUMN].count().reindex(hours, fill_value=0)
    output[PROFIT_SAMPLE_COUNT_COLUMN] = counts
    output[IMPUTED_SAMPLE_COUNT_COLUMN] = (INTERVALS_PER_HOUR - counts).where(counts.gt(0), 0)
    output[PROFIT_COVERAGE_COLUMN] = counts / INTERVALS_PER_HOUR
    # Scale only incomplete hours; complete hours retain the direct observed sum.
    output[PROFIT_COLUMN] = output[OBSERVED_PROFIT_COLUMN] * (
        INTERVALS_PER_HOUR / counts.where(counts.gt(0))
    )
    output[STATUS_COLUMN] = grouped[STATUS_COLUMN].agg(
        lambda values: "|".join(sorted(set(values.replace("", "<BLANK>"))))
    )
    output[STANDARD_TIME_COLUMN] = hours.tz_convert(TIMEZONE).tz_localize(None)
    output[UTC_HOUR_COLUMN] = hours.tz_localize(None)
    output["Month"] = experiment.payload["month"]
    output[RESOURCE_COLUMN] = member
    return output.loc[:, OUTPUT_COLUMNS].reset_index(drop=True)


def _write_excel_atomic(frame: pd.DataFrame, output_path: Path) -> None:
    """Write one workbook atomically so an old result is never half-written."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{output_path.stem}.",
            suffix=output_path.suffix,
            dir=output_path.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        with pd.ExcelWriter(temporary_path, engine="openpyxl") as writer:
            frame.to_excel(writer, sheet_name="Realized Profit", index=False)
            worksheet = writer.sheets["Realized Profit"]
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions
            for column_cells in worksheet.columns:
                column = column_cells[0].column_letter
                width = max(
                    14,
                    min(
                        32,
                        max(len(str(cell.value or "")) for cell in column_cells) + 2,
                    ),
                )
                worksheet.column_dimensions[column].width = width
        os.replace(temporary_path, output_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def run_experiment(
    experiment_path: Path | str = DEFAULT_EXPERIMENT_PATH,
    *,
    members: list[str] | None = None,
    results_dir: Path | None = None,
    month: str | None = None,
) -> tuple[Path, ...]:
    """Write hourly telemetry/profit workbooks for each selected member-month."""

    output_paths: list[Path] = []
    for selected_month in experiment_data.experiment_months(experiment_path, month):
        experiment = experiment_data.load_experiment(experiment_path, selected_month)
        if experiment.payload.get("schema_version") != 3:
            raise ValueError("hourly realized profit requires a monthly schema_version=3 config")
        selected_members = experiment_data.selected_names(experiment, members)
        output_dir = results_dir or experiment.output_root / DEFAULT_OUTPUT_SUBDIRECTORY
        for member in selected_members:
            source_path = _find_member_input(experiment.data_dir, member, selected_month)
            frame = _read_input(source_path)
            _validate_columns(frame, source_path)
            study = _select_study_rows(frame, source_path, member, experiment)
            normalized = _validate_numeric_inputs(study, source_path)
            output = _build_output(normalized, member, experiment)
            output_path = output_dir / experiment.output_name(
                f"Realized_Profit_{member}_1hour.xlsx"
            )
            _write_excel_atomic(output, output_path)
            output_paths.append(output_path)
            print(f"Done [{member}, {selected_month}]: {output_path}", flush=True)

    return tuple(output_paths)


# ---------------------------------------------------------------------------
# Command-line interface
# ---------------------------------------------------------------------------

def _build_argument_parser() -> argparse.ArgumentParser:
    """Define the explicit CLI entrypoint and its narrow overrides."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--experiment",
        type=Path,
        default=DEFAULT_EXPERIMENT_PATH,
        help="experiment configuration JSON",
    )
    parser.add_argument(
        "--member",
        action="append",
        default=None,
        help="selected member; repeat to process multiple members",
    )
    parser.add_argument("--month", help="YYYY-MM; omit to run every configured month")
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=None,
        help="override the configured realized-profit output directory",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the realized-profit export and report the created workbooks."""

    arguments = _build_argument_parser().parse_args(argv)
    paths = run_experiment(
        arguments.experiment,
        members=arguments.member,
        results_dir=arguments.results_dir,
        month=arguments.month,
    )
    print(f"Realized-profit workbooks written: {len(paths)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
