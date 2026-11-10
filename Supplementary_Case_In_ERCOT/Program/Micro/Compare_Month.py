"""Compare configured monthly hourly telemetry and Rolling profits.

Comparison sums every configured month for each resource, followed by an
ALL_RESOURCES total. Monthly_Detail retains each member-month calculation.
Final_SOC_Value_* in Comparison means the sum of monthly terminal SOC values,
not only the final month's inventory. Sum_* adds trading profit and that value;
initial inventory is not deducted and no efficiency adjustment is applied.

Telemetry uses exported Profit (including its hourly mean imputation), plus
the last in-month five-minute source SOC valued at the monthly mean hourly LMP.
Rolling uses its own Profit and final Modeled SOC After times the same monthly
mean LMP convention.
Missing inputs or invalid contributions abort before replacing the workbook.
"""

from __future__ import annotations

import argparse
import math
import os
import tempfile
from pathlib import Path

import pandas as pd
from openpyxl.styles import Font, PatternFill

import Experiment_Data_1hour as experiment_data


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_EXPERIMENT_PATH = BASE_DIR / "experiment_config_1hour.json"
UTC_HOUR = "Hour Start UTC"
UTC_SOURCE = "SCED Timestamp UTC"
RESOURCE = "Resource Name"
TOTAL_RESOURCE = "ALL_RESOURCES"
OUTPUT_COLUMNS = [
    "Resource",
    "Actual_Realized_Profit",
    "Final_SOC_Value_Realized",
    "Sum_Realized",
    "Actual_Rolling_Window_Profit",
    "Final_SOC_Value_Rolling",
    "Sum_Rolling",
]
VALUE_COLUMNS = OUTPUT_COLUMNS[1:]
MONTHLY_COLUMNS = ["Resource", "Month", *VALUE_COLUMNS]


def _number(frame: pd.DataFrame, column: str, path: Path) -> pd.Series:
    if column not in frame.columns:
        raise ValueError(f"{path}: missing required column {column!r}")
    values = pd.to_numeric(frame[column], errors="coerce")
    if not values.map(math.isfinite).all() or frame[column].map(
        lambda value: isinstance(value, bool)
    ).any():
        raise ValueError(f"{path}: column {column!r} contains missing or non-finite numbers")
    return values


def _read_columns(path: Path, columns: list[str], sheet_name: str | int = 0) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(f"missing required workbook: {path}")
    frame = pd.read_excel(
        path, sheet_name=sheet_name, usecols=lambda column: column in columns,
        keep_default_na=False,
    )
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise ValueError(f"{path}: missing required columns: {missing}")
    return frame


def _utc_times(frame: pd.DataFrame, column: str, path: Path) -> pd.Series:
    try:
        times = pd.to_datetime(frame[column], errors="raise", utc=True)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{path}: invalid {column}") from exc
    if times.isna().any():
        raise ValueError(f"{path}: missing {column}")
    return times


def _read_hourly(
    path: Path, resource: str, experiment: experiment_data.Experiment, *, rolling: bool
) -> pd.DataFrame:
    columns = [UTC_HOUR, RESOURCE, "Month", "LMP", "Profit"]
    if rolling:
        columns.append("Modeled SOC After")
    frame = _read_columns(path, columns)
    if not frame[RESOURCE].astype(str).str.strip().eq(resource).all():
        raise ValueError(f"{path}: Resource Name does not match {resource!r}")
    month = experiment.payload["month"]
    if not frame["Month"].eq(month).all():
        raise ValueError(f"{path}: Month does not match {month!r}")
    frame[UTC_HOUR] = _utc_times(frame, UTC_HOUR, path)
    frame = frame.sort_values(UTC_HOUR, kind="stable").reset_index(drop=True)
    expected = pd.date_range(
        experiment.start.tz_convert("UTC"),
        experiment.end_exclusive.tz_convert("UTC"),
        freq="h", inclusive="left",
    )
    actual = pd.DatetimeIndex(frame[UTC_HOUR])
    if actual.has_duplicates or not actual.equals(expected):
        raise ValueError(
            f"{path}: expected exactly {len(expected)} unique consecutive UTC hours "
            f"for local month {month}; found {len(frame)} rows"
        )
    for column in ("LMP", "Profit"):
        frame[column] = _number(frame, column, path)
    return frame


def _final_soc_value(frame: pd.DataFrame, path: Path, soc_column: str) -> float:
    soc = _number(frame.tail(1), soc_column, path).iloc[0]
    average_lmp = float(_number(frame, "LMP", path).mean())
    return float(soc * average_lmp)


def _realized_soc_value(
    realized: pd.DataFrame,
    realized_path: Path,
    soc_dir: Path,
    resource: str,
    experiment: experiment_data.Experiment,
) -> float:
    """Value the last actual source SOC at the monthly mean LMP."""
    month = experiment.payload["month"].replace("-", "")
    usage_path = soc_dir / f"{resource}_Usage_{month}.xlsx"
    usage = _read_columns(usage_path, [UTC_SOURCE, RESOURCE, "State of Charge"], "Usage")
    usage[UTC_SOURCE] = _utc_times(usage, UTC_SOURCE, usage_path)
    usage = usage.loc[
        usage[UTC_SOURCE].ge(experiment.start.tz_convert("UTC"))
        & usage[UTC_SOURCE].lt(experiment.end_exclusive.tz_convert("UTC"))
    ].sort_values(UTC_SOURCE, kind="stable")
    if usage.empty:
        raise ValueError(f"{usage_path}: no SOC source records within the study month")
    if not usage[RESOURCE].astype(str).str.strip().eq(resource).all():
        raise ValueError(f"{usage_path}: Resource Name does not match {resource!r}")
    if usage[UTC_SOURCE].dt.floor("5min").duplicated().any():
        raise ValueError(f"{usage_path}: duplicate five-minute UTC source slots")
    final_soc = _number(usage.tail(1), "State of Charge", usage_path).iloc[0]
    average_lmp = float(_number(realized, "LMP", realized_path).mean())
    return float(final_soc * average_lmp)


def _write_output(summary: pd.DataFrame, monthly: pd.DataFrame, output_path: Path) -> None:
    """Atomically publish both sheets only after every member-month succeeds."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{output_path.stem}.", suffix=".xlsx",
            dir=output_path.parent, delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        with pd.ExcelWriter(temporary_path, engine="openpyxl") as writer:
            writer.book.properties.description = (
                "Comparison: Final_SOC_Value_* sums monthly terminal SOC values; "
                "Sum_* = cumulative trading Profit + cumulative terminal SOC value. "
                "Monthly_Detail records the contributing member-month amounts. "
                "No initial inventory deduction or efficiency adjustment."
            )
            for name, frame in (("Comparison", summary), ("Monthly_Detail", monthly)):
                frame.to_excel(writer, index=False, sheet_name=name)
                sheet = writer.sheets[name]
                sheet.freeze_panes = "A2"
                sheet.auto_filter.ref = sheet.dimensions
                for cells in sheet.columns:
                    header = cells[0]
                    header.font = Font(bold=True, color="FFFFFF")
                    header.fill = PatternFill("solid", fgColor="203864")
                    sheet.column_dimensions[header.column_letter].width = max(
                        len(str(header.value)) + 2, 27 if header.value == "Resource" else 14
                    )
                    if header.value in VALUE_COLUMNS:
                        for cell in cells[1:]:
                            cell.number_format = "#,##0.00"
                if name == "Comparison":
                    for cell in sheet[sheet.max_row]:
                        cell.font = Font(bold=True)
        os.replace(temporary_path, output_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def compare(
    rolling_dir: Path | None = None,
    realized_dir: Path | None = None,
    output_path: Path | None = None,
    soc_dir: Path | None = None,
    *,
    experiment_path: Path | str = DEFAULT_EXPERIMENT_PATH,
) -> pd.DataFrame:
    """Export all configured months and return the summary including its total row."""
    months = experiment_data.experiment_months(experiment_path)
    experiments = [experiment_data.load_experiment(experiment_path, month) for month in months]
    if any(experiment.payload.get("schema_version") != 3 for experiment in experiments):
        raise ValueError("monthly comparison requires a schema_version=3 experiment")
    first = experiments[0]
    rolling_dir = Path(rolling_dir) if rolling_dir is not None else first.output_root / "rolling"
    realized_dir = Path(realized_dir) if realized_dir is not None else first.output_root / "realized_profit"
    soc_dir = Path(soc_dir) if soc_dir is not None else first.data_dir
    output_path = Path(output_path) if output_path is not None else first.output_root / "Comparison_Month.xlsx"
    resources = experiment_data.selected_names(first, None)
    if TOTAL_RESOURCE in resources:
        raise ValueError(f"{TOTAL_RESOURCE} is reserved for the summary total")
    rows = []
    for resource in resources:
        for experiment in experiments:
            month = experiment.payload["month"]
            token = month.replace("-", "")
            rolling_path = rolling_dir / f"{resource}_Rolling_Window_1hour_{token}.xlsx"
            realized_path = realized_dir / f"Realized_Profit_{resource}_1hour_{token}.xlsx"
            soc_path = soc_dir / f"{resource}_Usage_{token}.xlsx"
            if output_path.resolve() in {p.resolve() for p in (rolling_path, realized_path, soc_path)}:
                raise ValueError("comparison output must differ from all source workbooks")
            rolling = _read_hourly(rolling_path, resource, experiment, rolling=True)
            realized = _read_hourly(realized_path, resource, experiment, rolling=False)
            rolling_profit = float(rolling["Profit"].sum())
            realized_profit = float(realized["Profit"].sum())
            rolling_soc_value = _final_soc_value(rolling, rolling_path, "Modeled SOC After")
            realized_soc_value = _realized_soc_value(
                realized, realized_path, soc_dir, resource, experiment
            )
            row = {
                "Resource": resource,
                "Month": month,
                "Actual_Realized_Profit": realized_profit,
                "Final_SOC_Value_Realized": realized_soc_value,
                "Sum_Realized": realized_profit + realized_soc_value,
                "Actual_Rolling_Window_Profit": rolling_profit,
                "Final_SOC_Value_Rolling": rolling_soc_value,
                "Sum_Rolling": rolling_profit + rolling_soc_value,
            }
            if not all(math.isfinite(row[column]) for column in VALUE_COLUMNS):
                raise ValueError(f"{resource}, {month}: non-finite calculated comparison value")
            rows.append(row)
            print(f"Compared [{resource}, {month}]", flush=True)

    monthly = pd.DataFrame(rows, columns=MONTHLY_COLUMNS)
    result = monthly.groupby("Resource", sort=True)[VALUE_COLUMNS].sum().reset_index()
    total = {"Resource": TOTAL_RESOURCE, **result[VALUE_COLUMNS].sum().to_dict()}
    result = pd.concat([result, pd.DataFrame([total])], ignore_index=True)[OUTPUT_COLUMNS]
    if not result[VALUE_COLUMNS].map(math.isfinite).all().all():
        raise ValueError("non-finite six-month summary total")
    _write_output(result, monthly, output_path)
    print(
        f"Wrote {len(resources)} resources plus total and {len(monthly)} monthly rows "
        f"to {output_path}", flush=True,
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT_PATH)
    parser.add_argument("--rolling-dir", type=Path, help="override configured Rolling directory")
    parser.add_argument("--realized-dir", type=Path, help="override configured telemetry profit directory")
    parser.add_argument("--soc-dir", type=Path, help="override configured monthly Usage directory")
    parser.add_argument("--output", type=Path, help="default: configured output root / Comparison_Month.xlsx")
    args = parser.parse_args()
    compare(
        args.rolling_dir, args.realized_dir, args.output, args.soc_dir,
        experiment_path=args.experiment,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
