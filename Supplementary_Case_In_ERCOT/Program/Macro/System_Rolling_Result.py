"""Export monthly ERCOT fuel/curtailment counterfactuals for the selected ESRs.

Run from the project root with ``python -B Program/Macro/System_Rolling_Result.py``
or add ``--basis coal --month 2026-05``. The default basis is gas; thermal uses
a combined gas/coal merit stack. Inputs cover complete local months in UTC hours.
Positive ESR power is discharge. The selected generation pool and curtailment
proxy balance changes in the same 13 ESRs. Costs and CO2 are static merit-stack
estimates. Micro trading profits and terminal inventory values are preserved.
All requested months validate before any final workbook is replaced.
"""

from __future__ import annotations

import argparse
import calendar
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import tempfile

import numpy as np
import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.formatting.rule import CellIsRule
from pytz.exceptions import InvalidTimeError

from cost_calculation import CARBON_FACTORS_T_PER_MMBTU, FUELS_BY_BASIS, CostStack
from system_micro_inputs import MicroMonth, load_micro_month


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MONTHS = tuple(f"2026-{month:02d}" for month in range(1, 7))
LOCAL_ZONE = "America/Chicago"
LOCAL_TIME = "Interval Start"
UTC_TIME = "Hour Start UTC"
SCENARIOS = {
    "Actual": "_actual",
    "Rolling": "",
    "Without selected 13 ESRs": "_withoutESS",
}
SUMMARY_COLUMNS = ["Metric", "Unit", *SCENARIOS, "Definition", "Source", "SHA256"]
NUMERIC_ATOL = 1e-7
NUMERIC_RTOL = 1e-12
DATE_FORMAT = "yyyy-mm-dd hh:mm"
VALUE_FORMAT = "#,##0.000000"


@dataclass(frozen=True)
class BasisConfig:
    """Input stack and result vocabulary for one responding generation pool."""

    cost_directory: str
    workbook_prefix: str
    output_column: str
    label: str
    fixed_column: str | None = None
    fixed_label: str | None = None


BASES = {
    "gas": BasisConfig("ng_cost", "NG", "P_natural_gas", "NG", "P_coal", "Coal"),
    "coal": BasisConfig("cg_cost", "CG", "P_coal", "Coal", "P_natural_gas", "NG"),
    "thermal": BasisConfig("thermal_cost", "Thermal", "P_thermal", "Thermal"),
}


def _basis_config(basis: str) -> BasisConfig:
    if not isinstance(basis, str) or basis not in BASES:
        raise ValueError(f"basis must be gas, coal or thermal; received {basis!r}")
    return BASES[basis]


@dataclass(frozen=True)
class MonthlyResult:
    """Validated hourly values, monthly metrics and input fingerprints."""

    month: str
    rolling: pd.DataFrame
    summary: pd.DataFrame
    source_hashes: dict[Path, str]
    basis: str = "gas"


def expected_hours(month: str) -> pd.DatetimeIndex:
    """Return the real UTC hours in a YYYY-MM ERCOT local month, including DST."""
    if not isinstance(month, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise ValueError(f"invalid month {month!r}: expected YYYY-MM")
    start = pd.Timestamp(f"{month}-01", tz=LOCAL_ZONE)
    end = start + pd.offsets.MonthBegin(1)
    return pd.date_range(
        start.tz_convert("UTC"), end.tz_convert("UTC"), freq="h", inclusive="left"
    )


def _numbers(values, label: str, *, nonnegative: bool = False) -> np.ndarray:
    raw = np.asarray(values, dtype=object)
    if any(isinstance(value, (bool, np.bool_)) for value in raw.flat):
        raise ValueError(f"{label}: boolean values are not numbers")
    try:
        result = raw.astype(float)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label}: expected finite numeric values") from exc
    if not np.isfinite(result).all() or (nonnegative and (result < 0).any()):
        raise ValueError(
            f"{label}: expected finite {'nonnegative ' if nonnegative else ''}numeric values"
        )
    return result


def _read_excel_objects(path: Path, sheet: str | int) -> pd.DataFrame:
    """Preserve Excel cell types before numeric validation (including booleans)."""
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        worksheet = book.worksheets[sheet] if isinstance(sheet, int) else book[sheet]
        rows = worksheet.iter_rows(values_only=True)
        headers = next(rows, ())
        if not headers or len(set(headers)) != len(headers) or None in headers:
            raise ValueError(f"{path}: missing or duplicate column headers")
        return pd.DataFrame(list(rows), columns=headers, dtype=object)
    finally:
        book.close()


def read_system_hourly(
    path: Path, sheet: str, columns: tuple[str, ...], month: str
) -> pd.DataFrame:
    """Read local hourly sources, discard forecast tails and require exact UTC coverage.

    Ambiguous autumn local hours require a UTC source column; absent or duplicate
    spring hours are never filled. Extra next-month records do not enter results.
    """
    expected = expected_hours(month)
    frame = _read_excel_objects(path, sheet)
    missing = set((LOCAL_TIME, *columns)) - set(frame.columns)
    if missing:
        raise ValueError(f"{path}: missing columns {sorted(missing)}")
    try:
        local = pd.DatetimeIndex(pd.to_datetime(frame[LOCAL_TIME], errors="raise"))
        if local.tz is not None or local.hasnans:
            raise ValueError("expected populated naive ERCOT local dates")
        start = pd.Timestamp(f"{month}-01")
        end = start + pd.offsets.MonthBegin(1)
        selected = (local >= start) & (local < end)
        frame = frame.loc[selected].copy()
        local = local[selected]
        if UTC_TIME in frame:
            utc = pd.DatetimeIndex(
                pd.to_datetime(frame[UTC_TIME], errors="raise", utc=True)
            )
            if not utc.tz_convert(LOCAL_ZONE).tz_localize(None).equals(local):
                raise ValueError("UTC and local dates disagree")
        else:
            utc = local.tz_localize(
                LOCAL_ZONE, ambiguous="raise", nonexistent="raise"
            ).tz_convert("UTC")
    except (ValueError, TypeError, InvalidTimeError) as exc:
        raise ValueError(f"{path}: invalid hourly timestamps: {exc}") from exc
    if not utc.equals(expected):
        raise ValueError(
            f"{path}: expected exactly {len(expected)} ordered unique consecutive UTC hours for {month}, found {len(frame)}"
        )
    result = pd.DataFrame(index=utc)
    for column in columns:
        result[column] = _numbers(frame[column].to_numpy(), f"{path}: {column}")
    return result


def balance_scenario(gas, curtailment, delta) -> tuple[np.ndarray, np.ndarray]:
    """Balance an ESR change: Δgeneration - Δcurtailment + delta = 0.

    The legacy argument name ``gas`` accepts the selected gas, coal or thermal
    generation pool. Supply reduces this pool first; demand absorbs curtailment
    first. Inputs have identical shapes, generation/curtailment are nonnegative,
    and all values are finite MW. Returned values are nonnegative MW.
    """
    gas = _numbers(gas, "gas", nonnegative=True)
    curtailment = _numbers(curtailment, "curtailment", nonnegative=True)
    delta = _numbers(delta, "delta")
    if not (gas.shape == curtailment.shape == delta.shape):
        raise ValueError("gas, curtailment and delta must have identical shapes")
    demand = np.maximum(-delta, 0)
    absorbed = np.minimum(demand, curtailment)
    new_gas = np.maximum(gas - np.maximum(delta, 0), 0) + demand - absorbed
    new_curtailment = curtailment - absorbed + np.maximum(delta - gas, 0)
    residual = new_gas - gas - (new_curtailment - curtailment) + delta
    if not np.allclose(residual, 0, rtol=0, atol=NUMERIC_ATOL):
        raise ValueError("scenario energy balance failed")
    return new_gas, new_curtailment


def _sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _summary(
    rolling: pd.DataFrame,
    micro: MicroMonth,
    stack: CostStack,
    month: str,
    hashes: dict[Path, str],
    project_root: Path,
    basis: str = "gas",
) -> pd.DataFrame:
    config = _basis_config(basis)
    rows = []

    def add(metric, unit, values, definition=""):
        rows.append(
            {
                "Metric": metric,
                "Unit": unit,
                **dict(zip(SCENARIOS, values)),
                "Definition": definition,
                "Source": "",
                "SHA256": "",
            }
        )

    def totals(prefix):
        return [
            float(rolling[f"{prefix}{suffix}"].sum()) for suffix in SCENARIOS.values()
        ]

    add(
        f"{config.label}_Output",
        "MWh",
        totals(config.output_column),
        "One-hour average MW summed over real UTC hours",
    )
    if config.fixed_column:
        add(
            f"{config.fixed_label}_Output_Fixed",
            "MWh",
            [float(rolling[f"{config.fixed_column}_actual"].sum())] * 3,
            f"Observed {config.fixed_label} output held fixed in all scenarios",
        )
    else:
        for label, column in (("NG", "P_natural_gas"), ("Coal", "P_coal")):
            add(
                f"{label}_Observed_Output",
                "MWh",
                [float(rolling[f"{column}_actual"].sum()), "n.a.", "n.a."],
                "Observed component reference only; thermal scenarios evaluate the combined stack",
            )
    add(
        "Curtailment_Proxy",
        "MWh",
        totals("curtailment_proxy"),
        "Existing system wind plus solar HSL-based curtailment estimate",
    )
    add(
        "total_cost",
        "USD",
        totals("cost"),
        f"Modeled {config.label} fuel cost only, exact piecewise merit stack",
    )
    add(
        "total_carbon",
        "t CO2",
        totals("carbon"),
        f"Modeled {config.label} combustion CO2 only; heat rate times each primary_fuel factor",
    )
    costs, carbon = totals("cost"), totals("carbon")
    add(
        f"{config.label}_Fuel_Cost_Savings_vs_Actual",
        "USD",
        [0, costs[0] - costs[1], costs[0] - costs[2]],
    )
    add(
        f"{config.label}_CO2_Reduction_vs_Actual",
        "t CO2",
        [0, carbon[0] - carbon[1], carbon[0] - carbon[2]],
    )
    curt = totals("curtailment_proxy")
    add(
        "total_absorb",
        "MWh",
        [0, curt[0] - curt[1], curt[0] - curt[2]],
        "Signed additional renewable absorption relative to Actual",
    )
    income = micro.monthly_income
    add(
        "Trading_Profit",
        "USD",
        [
            income["Actual_Realized_Profit"],
            income["Actual_Rolling_Window_Profit"],
            "n.a.",
        ],
        "Sum of original Micro hourly Profit, including existing realized imputation",
    )
    add(
        "Final_SOC_Value",
        "USD",
        [income["Final_SOC_Value_Realized"], income["Final_SOC_Value_Rolling"], "n.a."],
        "Sum of each ESR terminal MWh times its own last hourly node LMP",
    )
    add(
        "total_profit",
        "USD",
        [income["Sum_Realized"], income["Sum_Rolling"], "n.a."],
        "Micro trading profit plus terminal value. No initial inventory deduction or efficiency adjustment",
    )
    excesses = [
        rolling[f"{basis}_excess_capacity_mw{suffix}"] for suffix in SCENARIOS.values()
    ]
    add(
        "Extrapolation_Hours",
        "hours",
        [int((values > 0).sum()) for values in excesses],
        f"{config.label} output above total modeled stack capacity",
    )
    add(
        "Extrapolation_Energy",
        "MWh",
        [float(values.sum()) for values in excesses],
        "Excess MW times one-hour duration",
    )
    add("Maximum_Excess_Capacity", "MW", [float(values.max()) for values in excesses])
    add(f"{config.label}_Stack_Capacity", "MW", [stack.capacity_mw] * 3)
    add("Basis", "", [basis] * 3)
    add(
        "Carbon_Factors",
        "t CO2/MMBtu",
        ["", "", ""],
        "; ".join(
            f"{fuel}={CARBON_FACTORS_T_PER_MMBTU[fuel]}"
            for fuel in FUELS_BY_BASIS[basis]
        ),
    )
    if basis == "thermal":
        add(
            "Thermal_Baseline_Method",
            "",
            ["", "", ""],
            "Actual observed NG + coal MW is evaluated on the combined cost-ranked stack. "
            "Actual cost/CO2 is a modeled pooled baseline, not the sum of separately evaluated observed fuel outputs",
        )
    add(
        "ESR_Net_Output",
        "MWh",
        [float(rolling["P_ESS_actual"].sum()), float(rolling["P_ESS"].sum()), 0],
        "Only the same configured 13 ESRs. Discharge positive, charge negative",
    )
    add("Month", "YYYY-MM", [month] * 3)
    add("Real_Hours", "hours", [len(rolling)] * 3)
    add("Control_Share", "fraction", [micro.control_share] * 3)
    add("Selected_ESR_Count", "resources", [len(micro.resources)] * 3)
    add(
        "Generated_UTC",
        "UTC",
        [datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")] * 3,
    )
    add("Selected_Resources", "names", ["", "", ""], ", ".join(micro.resources))
    add(
        "Scenario_Definition",
        "",
        [
            f"Observed {config.label} output and selected ESR telemetry; modeled cost/CO2",
            "Existing Micro Rolling dispatch",
            "Remove only selected 13 ESRs",
        ],
        "WithoutESS uses delta=-actual selected ESR output with the same balance rule",
    )
    add(
        "Balance_Method",
        "",
        ["", "", ""],
        f"Additional demand absorbs curtailment first. Additional supply reduces {basis} first. "
        + (
            f"{config.fixed_label} fixed. "
            if config.fixed_label
            else "Combined NG/coal merit order. "
        )
        + "Single-node static model",
    )
    add(
        "Exact_Method",
        "",
        ["", "", ""],
        "Stable cost ordering. Lower-cost block at a capacity breakpoint. Final block extrapolated above total capacity",
    )
    add(
        "Price_Definition",
        "USD/MWh",
        ["", "", ""],
        "Hub Official_Price is reference only. ESR profits retain original node LMP settlement",
    )
    add(
        "Time_Definition",
        "",
        ["", "", ""],
        "Interval Start is America/Chicago. Hour Start UTC is naive Excel date representing UTC. Target month only, no forecast tail",
    )
    for column in rolling.columns:
        unit = "MW"
        if column in (LOCAL_TIME, UTC_TIME):
            unit = "Excel date"
        elif column == "price" or column.startswith("marginal_price"):
            unit = "USD/MWh"
        elif column.startswith("profit") or column.startswith("cost"):
            unit = "USD/hour"
        elif column.startswith("carbon"):
            unit = "t CO2/hour"
        elif column.startswith(f"{basis}_extrapolated"):
            unit = "boolean"
        add(
            f"Field: {column}",
            unit,
            ["", "", ""],
            "Suffix _actual: Actual baseline (cost/CO2 modeled). No suffix: Rolling. _withoutESS: remove only the selected 13 ESRs",
        )
    for path, checksum in hashes.items():
        try:
            source = path.relative_to(project_root).as_posix()
        except ValueError:
            source = str(path)
        rows.append(
            {
                "Metric": "Input_Source",
                "Unit": "SHA256",
                **{name: "" for name in SCENARIOS},
                "Definition": "Validated local source",
                "Source": source,
                "SHA256": checksum,
            }
        )
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def build_month(project_root: Path, month: str, basis: str = "gas") -> MonthlyResult:
    """Compute one gas, coal or thermal month without writing any source files.

    Gas/coal hold the other fuel fixed. Thermal evaluates observed combined
    output on the pooled stack in all three scenarios, including Actual.
    """
    config = _basis_config(basis)
    expected = expected_hours(month)
    token = month.replace("-", "")
    year, month_number = map(int, month.split("-"))
    data = project_root / "Data" / "Macro"
    inputs = [
        (data / "ng_data" / f"ng_output_{token}.xlsx", "Output", ("NG_Output_MW",)),
        (data / "cg_data" / f"cg_output_{token}.xlsx", "Output", ("CG_Output_MW",)),
        (
            data / "Curtailment" / f"Curtailment_{token}.xlsx",
            "Curtailment",
            (
                "Wind Curtailment Proxy (MW)",
                "Solar Curtailment Proxy (MW)",
                "Total Curtailment Proxy (MW)",
            ),
        ),
        (
            data / "Price" / f"Price_{calendar.month_name[month_number]}.xlsx",
            "Price",
            ("Official_Price",),
        ),
    ]
    cost_path = (
        data
        / config.cost_directory
        / f"ERCOT_{config.workbook_prefix}_Final_{year}_{month_number:02d}.xlsx"
    )
    paths = [item[0] for item in inputs] + [cost_path]
    fingerprints = {path: _sha256(path) for path in paths}
    gas_frame, coal_frame, curt_frame, price_frame = [
        read_system_hourly(path, sheet, columns, month)
        for path, sheet, columns in inputs
    ]
    try:
        stack = CostStack.from_frame(
            _read_excel_objects(cost_path, 0), token, basis=basis
        )
    except ValueError as exc:
        raise ValueError(f"{cost_path}: {exc}") from exc
    micro = load_micro_month(project_root, month)
    if not micro.utc_index.equals(expected):
        raise ValueError(f"{month}: Micro and system UTC hours disagree")
    gas = _numbers(gas_frame["NG_Output_MW"], "NG_Output_MW", nonnegative=True)
    coal = _numbers(coal_frame["CG_Output_MW"], "CG_Output_MW", nonnegative=True)
    curt = _numbers(
        curt_frame["Total Curtailment Proxy (MW)"],
        "Total Curtailment Proxy (MW)",
        nonnegative=True,
    )
    wind = _numbers(
        curt_frame["Wind Curtailment Proxy (MW)"],
        "Wind Curtailment Proxy (MW)",
        nonnegative=True,
    )
    solar = _numbers(
        curt_frame["Solar Curtailment Proxy (MW)"],
        "Solar Curtailment Proxy (MW)",
        nonnegative=True,
    )
    if not np.allclose(wind + solar, curt, rtol=NUMERIC_RTOL, atol=NUMERIC_ATOL):
        raise ValueError(
            f"{month}: wind and solar proxies do not sum to total curtailment"
        )
    rolling_delta = micro.modeled_mw - micro.actual_mw
    without_delta = -micro.actual_mw
    generation = {"gas": gas, "coal": coal, "thermal": gas + coal}[basis]
    generation_rolling, curt_rolling = balance_scenario(generation, curt, rolling_delta)
    generation_without, curt_without = balance_scenario(generation, curt, without_delta)
    rolling = pd.DataFrame(
        {
            LOCAL_TIME: expected.tz_convert(LOCAL_ZONE).tz_localize(None),
            UTC_TIME: expected.tz_localize(None),
            "price": price_frame["Official_Price"].to_numpy(),
            "P_ESS": micro.modeled_mw,
            "P_ESS_actual": micro.actual_mw,
            "P_ESS_change": rolling_delta,
            "P_ESS_change_withoutESS": without_delta,
            "P_coal_actual": coal,
            "wind_curtailment_proxy_actual": wind,
            "solar_curtailment_proxy_actual": solar,
            "profit": micro.rolling_profit,
            "profit_actual": micro.actual_profit,
        }
    )
    if basis != "gas":
        rolling["P_natural_gas_actual"] = gas
    if basis == "coal":
        for suffix in ("", "_withoutESS"):
            rolling[f"P_natural_gas{suffix}"] = gas
    for suffix, scenario_generation, scenario_curt in zip(
        SCENARIOS.values(),
        (generation, generation_rolling, generation_without),
        (curt, curt_rolling, curt_without),
    ):
        evaluation = stack.evaluate(scenario_generation)
        rolling[f"{config.output_column}{suffix}"] = scenario_generation
        rolling[f"curtailment_proxy{suffix}"] = scenario_curt
        rolling[f"cost{suffix}"] = evaluation.cost
        rolling[f"marginal_price_{basis}{suffix}"] = evaluation.marginal_price
        rolling[f"carbon{suffix}"] = evaluation.carbon
        rolling[f"{basis}_excess_capacity_mw{suffix}"] = evaluation.excess_mw
        rolling[f"{basis}_extrapolated{suffix}"] = evaluation.excess_mw > 0
    rolling["P_renewable_absorbed"] = curt - curt_rolling
    rolling["P_renewable_absorbed_withoutESS"] = curt - curt_without
    rolling["cost_savings"] = rolling["cost_actual"] - rolling["cost"]
    rolling["carbon_reduction"] = rolling["carbon_actual"] - rolling["carbon"]
    for path in micro.sources:
        if path not in fingerprints:
            fingerprints[path] = micro.source_hashes.get(path) or _sha256(path)
    for path, checksum in fingerprints.items():
        if _sha256(path) != checksum:
            raise ValueError(f"input changed during processing: {path}")
    summary = _summary(rolling, micro, stack, month, fingerprints, project_root, basis)
    return MonthlyResult(month, rolling, summary, fingerprints, basis)


def _write_workbook(result: MonthlyResult, path: Path) -> None:
    """Write two typed result tables without indices or modifying source precision."""
    with pd.ExcelWriter(path, engine="openpyxl", datetime_format=DATE_FORMAT) as writer:
        for name, frame in (("Rolling", result.rolling), ("Summary", result.summary)):
            frame.to_excel(writer, sheet_name=name, index=False)
            sheet = writer.sheets[name]
            sheet.freeze_panes = "C2" if name == "Rolling" else "C2"
            sheet.auto_filter.ref = sheet.dimensions
            sheet.sheet_view.showGridLines = False
            sheet.sheet_properties.tabColor = (
                "203864" if name == "Summary" else "4472C4"
            )
            for cells in sheet.columns:
                header = cells[0]
                header.font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
                header.fill = PatternFill("solid", fgColor="203864")
                header.alignment = Alignment(
                    horizontal="center", vertical="center", wrap_text=True
                )
                width = max(20, min(len(str(header.value)) + 2, 36))
                if name == "Summary":
                    width = {
                        "Metric": 48,
                        "Unit": 18,
                        "Definition": 100,
                        "Source": 100,
                        "SHA256": 68,
                    }.get(str(header.value), 28)
                sheet.column_dimensions[header.column_letter].width = width
                for cell in cells[1:]:
                    cell.font = Font(name="Arial", size=10)
                    cell.alignment = Alignment(
                        vertical="center",
                        horizontal=(
                            "right"
                            if isinstance(cell.value, (int, float))
                            or cell.value == "n.a."
                            else "left"
                        ),
                        wrap_text=name == "Summary",
                    )
                    if isinstance(cell.value, datetime):
                        cell.number_format = DATE_FORMAT
                    elif isinstance(cell.value, (int, float)) and not isinstance(
                        cell.value, bool
                    ):
                        cell.number_format = VALUE_FORMAT
                if name == "Rolling" and str(header.value).startswith(
                    f"{result.basis}_excess_capacity_mw"
                ):
                    sheet.conditional_formatting.add(
                        f"{header.column_letter}2:{header.column_letter}{sheet.max_row}",
                        CellIsRule(
                            operator="greaterThan",
                            formula=["0"],
                            fill=PatternFill("solid", fgColor="FFF2CC"),
                        ),
                    )
            sheet.row_dimensions[1].height = 36
            if name == "Summary":
                for index, row in enumerate(frame.to_dict("records"), start=2):
                    length = len(str(row["Definition"]))
                    sheet.row_dimensions[index].height = max(
                        18, 15 * (1 + length // 100)
                    )
                    if row["Unit"] in ("hours", "resources"):
                        for cell in sheet[index][2:5]:
                            if isinstance(cell.value, (int, float)):
                                cell.number_format = "#,##0"
        # The legacy result-facing table order is intentional; Summary is the opening view.
        writer.book.active = writer.book.sheetnames.index("Summary")
        writer.book.properties.title = (
            f"ERCOT selected ESR {result.basis} system results {result.month}"
        )
        writer.book.properties.description = f"Static {result.basis} merit-stack and curtailment-proxy counterfactual. Micro profit plus monthly terminal value preserved. WithoutESS removes only selected 13 ESRs."


def verify_workbook(result: MonthlyResult, path: Path) -> None:
    """Reopen the export and compare every value, timestamp and requested feature."""
    with pd.ExcelFile(path) as workbook:
        if workbook.sheet_names != ["Rolling", "Summary"]:
            raise ValueError(f"{path}: expected only Rolling and Summary")
        for name, expected in (
            ("Rolling", result.rolling),
            ("Summary", result.summary),
        ):
            actual = pd.read_excel(workbook, sheet_name=name, keep_default_na=False)
            if (
                actual.columns.tolist() != expected.columns.tolist()
                or actual.shape != expected.shape
            ):
                raise ValueError(f"{path}, {name}: exported shape or columns changed")
            for column in expected.columns:
                source = expected[column]
                if pd.api.types.is_datetime64_any_dtype(source):
                    if not pd.DatetimeIndex(actual[column]).equals(
                        pd.DatetimeIndex(source)
                    ):
                        raise ValueError(
                            f"{path}, {name}: datetime mismatch in {column}"
                        )
                elif pd.api.types.is_bool_dtype(source):
                    if not np.array_equal(actual[column].to_numpy(), source.to_numpy()):
                        raise ValueError(
                            f"{path}, {name}: boolean mismatch in {column}"
                        )
                elif pd.api.types.is_numeric_dtype(source):
                    if not np.allclose(
                        actual[column].to_numpy(dtype=float),
                        source.to_numpy(dtype=float),
                        rtol=NUMERIC_RTOL,
                        atol=NUMERIC_ATOL,
                    ):
                        raise ValueError(
                            f"{path}, {name}: numeric mismatch in {column}"
                        )
                else:
                    for found, wanted in zip(actual[column], source):
                        if isinstance(wanted, (int, float)):
                            if not np.isclose(
                                float(found),
                                wanted,
                                rtol=NUMERIC_RTOL,
                                atol=NUMERIC_ATOL,
                            ):
                                raise ValueError(
                                    f"{path}, {name}: summary value mismatch in {column}"
                                )
                        elif found != wanted:
                            raise ValueError(
                                f"{path}, {name}: text mismatch in {column}"
                            )
    book = load_workbook(path, read_only=False, data_only=False)
    try:
        for sheet in book:
            if sheet.freeze_panes != "C2" or sheet.auto_filter.ref != sheet.dimensions:
                raise ValueError(f"{path}: missing freeze panes or full filter")
        for row in book["Rolling"].iter_rows(min_row=2, max_col=2):
            if any(
                not isinstance(cell.value, datetime) or not cell.is_date for cell in row
            ):
                raise ValueError(f"{path}: timestamp is not an Excel date")
    finally:
        book.close()


def export_results(results: list[MonthlyResult], output_dir: Path) -> list[Path]:
    """Publish validated months under a root: gas in place, other bases in subdirs."""
    destinations = []
    for result in results:
        _basis_config(result.basis)
        directory = output_dir if result.basis == "gas" else output_dir / result.basis
        destinations.append(
            directory / f"Rolling_Result_{result.month.replace('-', '')}.xlsx"
        )
    if len({path.resolve() for path in destinations}) != len(destinations):
        raise ValueError("duplicate basis/month output destinations")
    sources = {path.resolve() for result in results for path in result.source_hashes}
    if any(path.resolve() in sources for path in destinations):
        raise ValueError("output must differ from all source workbooks")
    output_dir.mkdir(parents=True, exist_ok=True)
    pending = []
    try:
        for result, final in zip(results, destinations):
            final.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                prefix=f".{final.stem}.", suffix=".xlsx", dir=final.parent, delete=False
            ) as handle:
                temporary = Path(handle.name)
            pending.append((temporary, final))
            _write_workbook(result, temporary)
            verify_workbook(result, temporary)
        hashes = {}
        for result in results:
            for path, checksum in result.source_hashes.items():
                if path in hashes and hashes[path] != checksum:
                    raise ValueError(f"input changed between months: {path}")
                hashes[path] = checksum
        for path, checksum in hashes.items():
            if _sha256(path) != checksum:
                raise ValueError(f"input changed before publication: {path}")
        for temporary, final in pending:
            os.replace(temporary, final)
        return [final for _, final in pending]
    finally:
        for temporary, _ in pending:
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--basis", choices=tuple(BASES), default="gas")
    parser.add_argument(
        "--month",
        choices=DEFAULT_MONTHS,
        help="process one configured 2026 month, default all January-June",
    )
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument(
        "--output-dir", type=Path, help="default Results/Macro_Results/System_Results"
    )
    args = parser.parse_args()
    root = args.project_root.resolve()
    months = (args.month,) if args.month else DEFAULT_MONTHS
    results = []
    for month in months:
        result = build_month(root, month, basis=args.basis)
        results.append(result)
        flags = [
            int(result.rolling[f"{args.basis}_extrapolated{suffix}"].sum())
            for suffix in SCENARIOS.values()
        ]
        print(
            f"Validated {args.basis} {month}: {len(result.rolling)} hours, extrapolation Actual/Rolling/Without13={flags}",
            flush=True,
        )
    destination = (
        args.output_dir or root / "Results" / "Macro_Results" / "System_Results"
    )
    for path in export_results(results, destination):
        print(f"Wrote and verified {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
