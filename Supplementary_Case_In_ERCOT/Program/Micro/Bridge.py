"""Aggregate monthly Micro Rolling dispatch into Macro ESR output workbooks.

Run ``python -B Program/Micro/Bridge.py`` for all configured months, or add
``--month 2026-03``. The experiment configuration owns the 13-member cohort.
Inputs are each member's Rolling sheet; no optimization or raw-data processing
is performed. Every member must cover the complete local month in UTC hours.

The Output sheet starts with Interval Start (America/Chicago) and ESR_Output_MW:
the signed sum of Cleared MW, positive for discharge and negative for charge.
Hour Start UTC disambiguates daylight-saving transitions. The remaining columns
retain the month and each member's MW contribution without rounding or scaling.
All selected inputs are validated before any existing output is replaced.
"""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl.styles import Font, PatternFill

import Experiment_Data_1hour as experiment_data
from project_paths import DEFAULT_EXPERIMENT_PATH, PROJECT_ROOT


DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "Results" / "Macro_Results" / "ESR_Output"
EXPECTED_MEMBER_COUNT = 13
UTC_HOUR = "Hour Start UTC"
LOCAL_TIME = "Interval Start"
TOTAL_POWER = "ESR_Output_MW"
SOURCE_COLUMNS = ("Time", UTC_HOUR, "Resource Name", "Month", "Cleared MW")


def _read_power(
    path: Path,
    member: str,
    experiment: experiment_data.Experiment,
    expected_hours: pd.DatetimeIndex,
) -> pd.Series:
    """Reject incomplete, duplicate, mislabeled, or non-finite contributions."""
    if not path.is_file():
        raise FileNotFoundError(f"missing required Rolling workbook: {path}")
    frame = pd.read_excel(
        path,
        sheet_name="Rolling",
        usecols=lambda column: column in SOURCE_COLUMNS,
        keep_default_na=False,
    )
    missing = sorted(set(SOURCE_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(f"{path}: missing required columns: {missing}")
    month = experiment.payload["month"]
    if not frame["Resource Name"].eq(member).all():
        raise ValueError(f"{path}: Resource Name does not match {member!r}")
    if not frame["Month"].eq(month).all():
        raise ValueError(f"{path}: Month does not match {month!r}")
    try:
        frame[UTC_HOUR] = pd.to_datetime(frame[UTC_HOUR], errors="raise", utc=True)
        frame["Time"] = pd.to_datetime(frame["Time"], errors="raise")
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{path}: invalid Rolling timestamps") from exc
    frame = frame.sort_values(UTC_HOUR).reset_index(drop=True)
    actual_hours = pd.DatetimeIndex(frame[UTC_HOUR])
    if not actual_hours.equals(expected_hours):
        raise ValueError(
            f"{path}: expected exactly {len(expected_hours)} unique consecutive UTC "
            f"hours for local month {month}; found {len(frame)} rows"
        )
    local_hours = expected_hours.tz_convert(experiment.start.tz).tz_localize(None)
    if not pd.DatetimeIndex(frame["Time"]).equals(local_hours):
        raise ValueError(f"{path}: Time does not match the local time of {UTC_HOUR}")
    values = pd.to_numeric(frame["Cleared MW"], errors="coerce")
    if (
        not np.isfinite(values.to_numpy(dtype=float)).all()
        or frame["Cleared MW"]
        .map(lambda value: isinstance(value, (bool, np.bool_)))
        .any()
    ):
        raise ValueError(f"{path}: Cleared MW contains missing or non-finite numbers")
    return pd.Series(values.to_numpy(dtype=float), index=expected_hours, name=member)


def aggregate_month(
    experiment: experiment_data.Experiment, rolling_dir: Path
) -> pd.DataFrame:
    """Return one full month of signed fleet MW and its 13 member contributions.

    Membership comes only from the validated schema-version-3 experiment;
    unrelated files in rolling_dir never expand or replace the configured cohort.
    """
    if experiment.payload.get("schema_version") != 3:
        raise ValueError("Bridge requires a schema_version=3 monthly experiment")
    members = experiment_data.selected_names(experiment, None)
    if len(members) != EXPECTED_MEMBER_COUNT:
        raise ValueError(
            f"Bridge requires {EXPECTED_MEMBER_COUNT} configured ESR members; "
            f"found {len(members)}"
        )
    reserved = {LOCAL_TIME, TOTAL_POWER, UTC_HOUR, "Month"}
    if reserved.intersection(members):
        raise ValueError(
            f"member names conflict with output columns: {reserved.intersection(members)}"
        )
    month = experiment.payload["month"]
    token = month.replace("-", "")
    expected_hours = pd.date_range(
        experiment.start.tz_convert("UTC"),
        experiment.end_exclusive.tz_convert("UTC"),
        freq="h",
        inclusive="left",
    )
    contributions = pd.concat(
        [
            _read_power(
                Path(rolling_dir) / f"{member}_Rolling_Window_1hour_{token}.xlsx",
                member,
                experiment,
                expected_hours,
            )
            for member in members
        ],
        axis=1,
    )
    total = contributions.sum(axis=1, skipna=False)
    if not np.isfinite(total.to_numpy()).all():
        raise ValueError(f"{month}: aggregated ESR output is not finite")
    result = contributions.reset_index(drop=True)
    result.insert(0, "Month", month)
    result.insert(0, UTC_HOUR, expected_hours.tz_localize(None))
    result.insert(0, TOTAL_POWER, total.to_numpy())
    result.insert(
        0, LOCAL_TIME, expected_hours.tz_convert(experiment.start.tz).tz_localize(None)
    )
    return result


def _write_output(frame: pd.DataFrame, output_path: Path) -> None:
    """Replace a monthly workbook only after its complete export succeeds."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{output_path.stem}.",
            suffix=".xlsx",
            dir=output_path.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        with pd.ExcelWriter(temporary_path, engine="openpyxl") as writer:
            writer.book.properties.description = (
                "ESR_Output_MW sums the configured 13 ESRs' Rolling Cleared MW "
                "at each Hour Start UTC. Positive = discharge, negative = charge. "
                "Interval Start is America/Chicago local time; UTC is timezone-naive "
                "UTC for Excel. Member columns are signed MW, without scaling."
            )
            frame.to_excel(writer, sheet_name="Output", index=False)
            sheet = writer.sheets["Output"]
            sheet.freeze_panes = "C2"
            sheet.auto_filter.ref = sheet.dimensions
            for cells in sheet.columns:
                header = cells[0]
                header.font = Font(bold=True, color="FFFFFF")
                header.fill = PatternFill("solid", fgColor="203864")
                is_time = header.value in (LOCAL_TIME, UTC_HOUR)
                sheet.column_dimensions[header.column_letter].width = max(
                    len(str(header.value)) + 2, 23 if is_time else 14
                )
                if header.value != "Month":
                    for cell in cells[1:]:
                        cell.number_format = (
                            "yyyy-mm-dd hh:mm" if is_time else "#,##0.000000"
                        )
        os.replace(temporary_path, output_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def bridge(
    experiment_path: Path | str = DEFAULT_EXPERIMENT_PATH,
    *,
    month: str | None = None,
    rolling_dir: Path | None = None,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> list[Path]:
    """Validate all selected months, export ESR_Rolling_Output_YYYYMM.xlsx files.

    Omit month to use every configured month. Default inputs follow the config's
    output_root/rolling; default outputs are anchored to the project Macro results.
    Returns the saved workbook paths. Requires pandas, numpy and openpyxl only.
    """
    pending = []
    for selected_month in experiment_data.experiment_months(experiment_path, month):
        experiment = experiment_data.load_experiment(experiment_path, selected_month)
        source_dir = (
            Path(rolling_dir)
            if rolling_dir is not None
            else experiment.output_root / "rolling"
        )
        frame = aggregate_month(experiment, source_dir)
        token = experiment.payload["month"].replace("-", "")
        path = Path(output_dir) / f"ESR_Rolling_Output_{token}.xlsx"
        pending.append((path, frame))
        print(
            f"Validated {selected_month}: {EXPECTED_MEMBER_COUNT} ESRs, {len(frame)} hours",
            flush=True,
        )
    for path, frame in pending:
        _write_output(frame, path)
        print(f"Saved {path}", flush=True)
    return [path for path, _ in pending]


def main(argv: list[str] | None = None) -> int:
    """Run the monthly Micro-to-Macro aggregation from any working directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT_PATH)
    parser.add_argument("--month", help="YYYY-MM; omit for all configured months")
    parser.add_argument(
        "--rolling-dir", type=Path, help="Override the Rolling input directory"
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    bridge(
        args.experiment,
        month=args.month,
        rolling_dir=args.rolling_dir,
        output_dir=args.output_dir,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Error: {exc}") from None
