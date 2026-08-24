"""Build canonical monthly gas stacks with grouped CA/CT blocks.

The command preserves every non-CA/CT row from the ungrouped monthly base,
but rebuilds combined-cycle rows from the uncleaned EIA-923 ``Month_Agg``
workbooks.  EIA-860 ``Plant Code + Unit Code`` identifies each combined-cycle
block.  Monthly fuel use remains at the EIA-923 plant scope, so multiple Unit
Codes at one plant share the same explicitly labelled plant-level intensity.

``mmbtu_per_mwh`` is derived directly for every output row.  The default output
is the canonical ``data/ng_cost`` directory.  Each workbook is replaced
atomically, and this module never reads or runs the battery optimization.
"""

from __future__ import annotations

import argparse
import calendar
import math
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from calculate_final_monthly import read_monthly_fuel_costs


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent.parent
DEFAULT_MONTH_AGG_DIR = BASE_DIR / "Month_Agg"
DEFAULT_BASE_STACK_DIR = BASE_DIR / "Month_Agg_Clear_V2"
DEFAULT_GENERATOR_DIR = BASE_DIR / "Data"
DEFAULT_FUEL_COST_FILE = BASE_DIR / "Month_Agg_Clear" / "Fuel_Cost.xls"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "ng_cost"
DEFAULT_START = (2023, 1)
DEFAULT_END = (2025, 12)

MONTH_FILE_PATTERN = "CAISO_NG_{year:04d}_{month:02d}.xlsx"
FINAL_FILE_PATTERN = "CAISO_NG_Final_{year:04d}_{month:02d}.xlsx"
YEAR_MONTH_PATTERN = re.compile(r"^(\d{4})-(\d{2})$")
CC_PRIME_MOVERS = frozenset({"CA", "CT"})
REPLACED_PRIME_MOVERS = frozenset({"CA", "CT", "CC_GROUPED"})
CARBON_FACTOR_MTCO2_PER_MMBTU = 0.05306

GENERATOR_REQUIRED_COLUMNS = (
    "Plant Code",
    "Plant Name",
    "Generator ID",
    "Prime Mover",
    "Unit Code",
    "Nameplate Capacity (MW)",
    "Minimum Load (MW)",
)
MONTHLY_REQUIRED_COLUMNS = (
    "YEAR",
    "MONTH",
    "Plant Id",
    "Plant Name",
    "Reported Prime Mover",
    "Quantity",
    "Elec_Quantity",
    "Tot_MMBtu",
    "Elec_MMBtu",
    "Netgen",
)
BASE_STACK_REQUIRED_COLUMNS = (
    "Plant Id",
    "Reported Prime Mover",
    "Elec_MMBtu",
    "Netgen",
    "capacity",
    "$_per_mwh",
)
FINAL_STACK_REQUIRED_COLUMNS = (
    "Plant Id",
    "Reported Prime Mover",
    "capacity",
    "$_per_mwh",
    "mmbtu_per_mwh",
)
STACK_IDENTITY_COLUMNS = (
    "block_id",
    "block_type",
    "unit_code",
    "member_generator_ids",
    "selected_generator_ids",
    "source_scope",
    "allocation_share",
)
EXTENSIVE_COLUMNS = (
    "Quantity",
    "Elec_Quantity",
    "Tot_MMBtu",
    "Elec_MMBtu",
    "Netgen",
)

YearMonth = tuple[int, int]


@dataclass(frozen=True)
class MonthBuildResult:
    """Counts and conservation diagnostics for one generated month."""

    year: int
    month: int
    output_rows: int
    legacy_rows: int
    cc_rows: int
    skipped_cc_plants: int
    max_conservation_error: float


def parse_year_month(value: str) -> YearMonth:
    """Parse one inclusive ``YYYY-MM`` command-line value."""
    match = YEAR_MONTH_PATTERN.fullmatch(value.strip())
    if match is None:
        raise argparse.ArgumentTypeError(
            f"Year and month must use YYYY-MM format: {value!r}"
        )
    year, month = (int(part) for part in match.groups())
    if not 1 <= month <= 12:
        raise argparse.ArgumentTypeError(f"Invalid year and month: {value!r}")
    return year, month


def iter_year_months(start: YearMonth, end: YearMonth):
    """Yield inclusive year/month pairs in chronological order."""
    if start > end:
        raise ValueError(f"Start month {start} is later than end month {end}")
    year, month = start
    while (year, month) <= end:
        yield year, month
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def normalize_identifier(value: object) -> str:
    """Return a stable non-empty identifier string, or an empty string."""
    if value is None or pd.isna(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def normalize_plant_id(value: object) -> int:
    """Return one integral EIA plant identifier."""
    numeric = pd.to_numeric(value, errors="coerce")
    if pd.isna(numeric) or not math.isfinite(float(numeric)):
        raise ValueError(f"Invalid Plant Code: {value!r}")
    integer = int(numeric)
    if float(numeric) != integer:
        raise ValueError(f"Plant Code must be integral: {value!r}")
    return integer


def require_columns(
    frame: pd.DataFrame, required: tuple[str, ...], source: Path | str
) -> None:
    """Fail at the file boundary when a required schema field is absent."""
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise KeyError(f"{source} is missing columns: {', '.join(missing)}")


def find_header_row(
    workbook: Path,
    sheet_name: str,
    required_columns: tuple[str, ...],
) -> int:
    """Locate an EIA header row despite annual release-note preambles."""
    preview = pd.read_excel(
        workbook,
        sheet_name=sheet_name,
        header=None,
        nrows=12,
        engine="openpyxl",
    )
    required = set(required_columns)
    for row_index, row in preview.iterrows():
        values = {str(value).strip() for value in row if pd.notna(value)}
        if required.issubset(values):
            return int(row_index)
    raise ValueError(
        f"Could not locate the EIA-860 header in {workbook} [{sheet_name}]"
    )


def read_generator_crosswalk(
    generator_file: Path,
    year: int,
    plant_ids: set[int] | None = None,
) -> pd.DataFrame:
    """Read and validate CA/CT generator membership from one EIA-860 year."""
    if not generator_file.is_file():
        raise FileNotFoundError(f"EIA-860 generator file not found: {generator_file}")
    header_row = find_header_row(generator_file, "Operable", GENERATOR_REQUIRED_COLUMNS)
    frame = pd.read_excel(
        generator_file,
        sheet_name="Operable",
        header=header_row,
        engine="openpyxl",
    )
    require_columns(frame, GENERATOR_REQUIRED_COLUMNS, generator_file)
    frame = frame.loc[
        frame["Prime Mover"].astype(str).str.strip().str.upper().isin(CC_PRIME_MOVERS),
        list(GENERATOR_REQUIRED_COLUMNS),
    ].copy()
    frame["Plant Code"] = frame["Plant Code"].map(normalize_plant_id)
    if plant_ids is not None:
        frame = frame[frame["Plant Code"].isin(plant_ids)].copy()
    for column in ("Generator ID", "Prime Mover", "Unit Code"):
        frame[column] = frame[column].map(normalize_identifier)
    frame["Prime Mover"] = frame["Prime Mover"].str.upper()
    frame["Nameplate Capacity (MW)"] = pd.to_numeric(
        frame["Nameplate Capacity (MW)"], errors="coerce"
    )
    frame["Minimum Load (MW)"] = pd.to_numeric(
        frame["Minimum Load (MW)"], errors="coerce"
    )
    invalid = (
        frame["Generator ID"].eq("")
        | frame["Unit Code"].eq("")
        | frame["Nameplate Capacity (MW)"].isna()
        | frame["Nameplate Capacity (MW)"].le(0)
    )
    if invalid.any():
        rows = frame.index[invalid].tolist()[:10]
        raise ValueError(
            f"Invalid CA/CT EIA-860 identity or capacity in {generator_file}; "
            f"source row indices: {rows}"
        )
    duplicate = frame.duplicated(["Plant Code", "Generator ID"], keep=False)
    if duplicate.any():
        keys = frame.loc[duplicate, ["Plant Code", "Generator ID"]].head(10)
        raise ValueError(
            "Duplicate EIA-860 generator identifiers: "
            + keys.astype(str).agg(":".join, axis=1).str.cat(sep=", ")
        )

    frame = frame.reset_index(drop=True)
    frame.insert(0, "YEAR", year)
    frame["block_id"] = frame.apply(
        lambda row: f"CC:{row['Plant Code']}:{row['Unit Code']}", axis=1
    )
    frame["source_order"] = np.arange(len(frame), dtype=int)

    mover_sets = frame.groupby(["Plant Code", "Unit Code"])["Prime Mover"].agg(set)
    incomplete = mover_sets[
        mover_sets.map(lambda movers: not CC_PRIME_MOVERS.issubset(movers))
    ]
    if not incomplete.empty:
        formatted = ", ".join(
            f"{plant}:{unit}" for plant, unit in incomplete.index[:10]
        )
        raise ValueError(f"EIA-860 CA/CT Unit Code groups are incomplete: {formatted}")
    return frame


def collect_cc_plant_ids(month_agg_dir: Path, year: int, months: list[int]) -> set[int]:
    """Collect the research-scope CA/CT plants before validating EIA-860."""
    plant_ids: set[int] = set()
    for month in months:
        source = month_agg_dir / MONTH_FILE_PATTERN.format(year=year, month=month)
        if not source.is_file():
            raise FileNotFoundError(f"Month_Agg workbook not found: {source}")
        frame = pd.read_excel(
            source,
            usecols=["Plant Id", "Reported Prime Mover"],
            engine="openpyxl",
        )
        movers = frame["Reported Prime Mover"].astype(str).str.strip().str.upper()
        for value in frame.loc[movers.isin(CC_PRIME_MOVERS), "Plant Id"].dropna():
            plant_ids.add(normalize_plant_id(value))
    return plant_ids


def select_generators_for_average(
    generators: pd.DataFrame, average_capacity: float
) -> pd.DataFrame:
    """Apply the legacy capacity rule: cover average, then add one reserve."""
    if not math.isfinite(average_capacity) or average_capacity <= 0:
        return generators.iloc[0:0].copy()
    ordered = generators.sort_values("source_order", kind="stable")
    if ordered.empty:
        return ordered.copy()
    capacities = ordered["Nameplate Capacity (MW)"].to_numpy(float)
    cumulative = np.cumsum(capacities)
    reached = np.flatnonzero(cumulative >= average_capacity)
    last_index = int(reached[0]) if len(reached) else len(ordered) - 1
    if last_index + 1 < len(ordered):
        last_index += 1
    return ordered.iloc[: last_index + 1].copy()


def calculate_combined_rates(
    electric_quantity: float,
    electric_mmbtu: float,
    net_generation: float,
    fuel_price: float,
) -> tuple[float, float, float]:
    """Return Mcf/MWh, MMBtu/MWh, and fuel cost after summing raw amounts."""
    values = (electric_quantity, electric_mmbtu, net_generation, fuel_price)
    if not all(math.isfinite(value) for value in values):
        raise ValueError(f"Non-finite combined-cycle input values: {values}")
    if electric_quantity <= 0 or electric_mmbtu <= 0 or net_generation <= 0:
        raise ValueError(
            "Combined-cycle Elec_Quantity, Elec_MMBtu, and Netgen must be positive"
        )
    mcf_per_mwh = electric_quantity / net_generation
    mmbtu_per_mwh = electric_mmbtu / net_generation
    return mcf_per_mwh, mmbtu_per_mwh, mcf_per_mwh * fuel_price


def common_value(group: pd.DataFrame, column: str) -> object:
    """Return a shared source value, falling back to the first non-null value."""
    non_null = group[column].dropna()
    if non_null.empty:
        return np.nan
    return non_null.iloc[0]


def build_cc_rows(
    raw_cc: pd.DataFrame,
    crosswalk: pd.DataFrame,
    fuel_price: float,
    stack_columns: list[str],
    year: int,
    month: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Derive monthly Unit-Code blocks and plant-level conservation records."""
    require_columns(raw_cc, MONTHLY_REQUIRED_COLUMNS, "Month_Agg frame")
    hours = float(calendar.monthrange(year, month)[1] * 24)
    rows: list[dict[str, object]] = []
    audits: list[dict[str, object]] = []

    numeric_raw = raw_cc.copy()
    for column in EXTENSIVE_COLUMNS:
        numeric_raw[column] = pd.to_numeric(numeric_raw[column], errors="coerce")

    for plant_value, plant_group in numeric_raw.groupby("Plant Id", sort=False):
        plant_id = normalize_plant_id(plant_value)
        movers = set(
            plant_group["Reported Prime Mover"].astype(str).str.strip().str.upper()
        )
        if not CC_PRIME_MOVERS.issubset(movers):
            raise ValueError(
                f"{year}-{month:02d} plant {plant_id} lacks a raw CA or CT row"
            )

        totals = {
            column: float(plant_group[column].sum(min_count=1))
            for column in EXTENSIVE_COLUMNS
        }
        valid = all(
            math.isfinite(totals[column]) and totals[column] > 0
            for column in ("Elec_Quantity", "Elec_MMBtu", "Netgen")
        )
        if not valid:
            audits.append(
                {
                    "YEAR": year,
                    "MONTH": month,
                    "Plant Id": plant_id,
                    "Plant Name": common_value(plant_group, "Plant Name"),
                    "status": "skipped_nonpositive_combined_amounts",
                    "block_count": 0,
                    **{f"source_{key}": value for key, value in totals.items()},
                }
            )
            continue

        mcf_per_mwh, mmbtu_per_mwh, cost_per_mwh = calculate_combined_rates(
            totals["Elec_Quantity"],
            totals["Elec_MMBtu"],
            totals["Netgen"],
            fuel_price,
        )
        plant_generators = crosswalk[crosswalk["Plant Code"].eq(plant_id)]
        if plant_generators.empty:
            raise KeyError(
                f"No EIA-860 CA/CT generators for {year}-{month:02d} plant {plant_id}"
            )

        selected_parts: list[pd.DataFrame] = []
        for mover in sorted(CC_PRIME_MOVERS):
            mover_generation = float(
                plant_group.loc[
                    plant_group["Reported Prime Mover"]
                    .astype(str)
                    .str.strip()
                    .str.upper()
                    .eq(mover),
                    "Netgen",
                ].sum()
            )
            selected = select_generators_for_average(
                plant_generators[plant_generators["Prime Mover"].eq(mover)],
                mover_generation / hours,
            )
            if not selected.empty:
                selected_parts.append(selected)
        if not selected_parts:
            raise ValueError(
                f"No monthly CA/CT capacity selected for {year}-{month:02d} "
                f"plant {plant_id}"
            )
        selected_generators = pd.concat(selected_parts, ignore_index=True)

        blocks: list[dict[str, object]] = []
        for unit_code, selected_group in selected_generators.groupby(
            "Unit Code", sort=False
        ):
            full_group = plant_generators[plant_generators["Unit Code"].eq(unit_code)]
            capacity = float(selected_group["Nameplate Capacity (MW)"].sum())
            minimum_values = selected_group["Minimum Load (MW)"].dropna()
            blocks.append(
                {
                    "unit_code": unit_code,
                    "block_id": f"CC:{plant_id}:{unit_code}",
                    "capacity": capacity,
                    "minimum_load": (
                        float(minimum_values.sum())
                        if not minimum_values.empty
                        else np.nan
                    ),
                    "member_generator_ids": ",".join(
                        full_group["Generator ID"].astype(str)
                    ),
                    "selected_generator_ids": ",".join(
                        selected_group["Generator ID"].astype(str)
                    ),
                }
            )

        total_capacity = sum(block["capacity"] for block in blocks)
        if not math.isfinite(total_capacity) or total_capacity <= 0:
            raise ValueError(
                f"Invalid grouped capacity for {year}-{month:02d} plant {plant_id}"
            )

        allocated = {column: 0.0 for column in EXTENSIVE_COLUMNS}
        for block in blocks:
            share = block["capacity"] / total_capacity
            output = {column: np.nan for column in stack_columns}
            for column in raw_cc.columns:
                if column in output and column not in EXTENSIVE_COLUMNS:
                    output[column] = common_value(plant_group, column)
            for column in EXTENSIVE_COLUMNS:
                output[column] = totals[column] * share
                allocated[column] += float(output[column])

            output.update(
                {
                    "YEAR": year,
                    "MONTH": month,
                    "Plant Id": plant_id,
                    "Reported Prime Mover": "CC_GROUPED",
                    "Physical Unit Label": "mcf",
                    "MMBtuPer_Unit": totals["Elec_MMBtu"] / totals["Elec_Quantity"],
                    "mmbtu_per_mwh": mmbtu_per_mwh,
                    "Mcf_per_MWh": mcf_per_mwh,
                    "average_capacity": output["Netgen"] / hours,
                    "capacity": block["capacity"],
                    "Minimum Load (MW)": block["minimum_load"],
                    "$_per_mwh": cost_per_mwh,
                    "block_id": block["block_id"],
                    "block_type": "combined_cycle_unit_code",
                    "unit_code": block["unit_code"],
                    "member_generator_ids": block["member_generator_ids"],
                    "selected_generator_ids": block["selected_generator_ids"],
                    "source_scope": "EIA923_plant_CA_CT_capacity_share",
                    "allocation_share": share,
                }
            )
            rows.append(output)

        audit = {
            "YEAR": year,
            "MONTH": month,
            "Plant Id": plant_id,
            "Plant Name": common_value(plant_group, "Plant Name"),
            "status": "grouped",
            "block_count": len(blocks),
            "combined_capacity": total_capacity,
            "Mcf_per_MWh": mcf_per_mwh,
            "mmbtu_per_mwh": mmbtu_per_mwh,
            "$_per_mwh": cost_per_mwh,
            "carbon_t_per_mwh": mmbtu_per_mwh * CARBON_FACTOR_MTCO2_PER_MMBTU,
        }
        for column in EXTENSIVE_COLUMNS:
            audit[f"source_{column}"] = totals[column]
            audit[f"allocated_{column}"] = allocated[column]
            audit[f"delta_{column}"] = allocated[column] - totals[column]
        audits.append(audit)

    return pd.DataFrame(rows), pd.DataFrame(audits)


def select_non_cc_base_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """Return rows that must survive a rebuild, including an idempotent rerun."""
    movers = frame["Reported Prime Mover"].astype(str).str.strip().str.upper()
    replaced = movers.isin(REPLACED_PRIME_MOVERS)
    if "block_type" in frame.columns:
        replaced |= (
            frame["block_type"].astype(str).str.strip().eq("combined_cycle_unit_code")
        )
    return frame.loc[~replaced].copy()


def derive_mmbtu_per_mwh(frame: pd.DataFrame, source: Path | str) -> pd.DataFrame:
    """Derive heat input intensity directly from EIA-923 extensive quantities."""
    require_columns(frame, ("Elec_MMBtu", "Netgen"), source)
    heat_input = pd.to_numeric(frame["Elec_MMBtu"], errors="coerce")
    net_generation = pd.to_numeric(frame["Netgen"], errors="coerce")
    intensity = heat_input / net_generation

    result = frame.drop(columns=["mmbtu_per_mwh"], errors="ignore").copy()
    insert_at = result.columns.get_loc("Netgen") + 1
    result.insert(insert_at, "mmbtu_per_mwh", intensity)
    return result


def add_legacy_block_identity(frame: pd.DataFrame) -> pd.DataFrame:
    """Annotate retained non-CA/CT rows with stable dispatch identities."""
    result = frame.copy()
    identifiers = []
    for row_number, (_, row) in enumerate(result.iterrows(), start=1):
        plant = normalize_identifier(row["Plant Id"])
        mover = normalize_identifier(row["Reported Prime Mover"]).upper()
        identifiers.append(f"LEGACY:{plant}:{mover}:{row_number}")
    result["block_id"] = identifiers
    result["block_type"] = "legacy_plant_prime_mover"
    result["unit_code"] = ""
    result["member_generator_ids"] = ""
    result["selected_generator_ids"] = ""
    result["source_scope"] = "ungrouped_final_non_cc"
    result["allocation_share"] = 1.0
    return result


def validate_generated_stack(frame: pd.DataFrame, source: str) -> None:
    """Validate the final dispatch schema and all cost-critical values."""
    require_columns(
        frame,
        FINAL_STACK_REQUIRED_COLUMNS + STACK_IDENTITY_COLUMNS,
        source,
    )
    if frame.empty:
        raise ValueError(f"Generated stack is empty: {source}")
    if frame["block_id"].duplicated().any():
        duplicates = frame.loc[frame["block_id"].duplicated(False), "block_id"]
        raise ValueError(
            f"Duplicate block_id values in {source}: "
            + ", ".join(duplicates.astype(str).head(10))
        )
    for column in ("capacity", "$_per_mwh", "mmbtu_per_mwh"):
        numeric = pd.to_numeric(frame[column], errors="coerce")
        invalid = ~np.isfinite(numeric) | numeric.le(0)
        if invalid.any():
            blocks = frame.loc[invalid, "block_id"].astype(str).head(10)
            raise ValueError(
                f"Invalid positive finite {column} in {source}: " + ", ".join(blocks)
            )


def write_excel_atomically(frame: pd.DataFrame, output_file: Path) -> None:
    """Replace one XLSX only after a complete workbook has been saved."""
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


def build_month(
    year: int,
    month: int,
    month_agg_dir: Path,
    base_stack_dir: Path,
    output_dir: Path,
    audit_dir: Path | None,
    crosswalk: pd.DataFrame,
    fuel_price: float,
) -> MonthBuildResult:
    """Build, validate, and atomically save one canonical monthly workbook."""
    raw_path = month_agg_dir / MONTH_FILE_PATTERN.format(year=year, month=month)
    base_path = base_stack_dir / FINAL_FILE_PATTERN.format(year=year, month=month)
    if not raw_path.is_file():
        raise FileNotFoundError(f"Month_Agg workbook not found: {raw_path}")
    if not base_path.is_file():
        raise FileNotFoundError(f"Ungrouped base workbook not found: {base_path}")

    raw = pd.read_excel(raw_path, engine="openpyxl")
    base = pd.read_excel(base_path, engine="openpyxl")
    require_columns(raw, MONTHLY_REQUIRED_COLUMNS, raw_path)
    require_columns(base, BASE_STACK_REQUIRED_COLUMNS, base_path)

    raw_movers = raw["Reported Prime Mover"].astype(str).str.strip().str.upper()
    raw_cc = raw.loc[raw_movers.isin(CC_PRIME_MOVERS)].copy()
    legacy = add_legacy_block_identity(
        derive_mmbtu_per_mwh(select_non_cc_base_rows(base), base_path)
    )

    stack_columns = list(legacy.columns)
    for column in STACK_IDENTITY_COLUMNS:
        if column not in stack_columns:
            stack_columns.append(column)
    cc_rows, audit = build_cc_rows(
        raw_cc,
        crosswalk,
        fuel_price,
        stack_columns,
        year,
        month,
    )
    for column in stack_columns:
        if column not in legacy.columns:
            legacy[column] = np.nan
        if column not in cc_rows.columns:
            cc_rows[column] = np.nan
    combined = pd.concat(
        [legacy[stack_columns], cc_rows[stack_columns]], ignore_index=True
    )
    combined = combined.sort_values("$_per_mwh", kind="stable").reset_index(drop=True)
    validate_generated_stack(combined, f"{year}-{month:02d}")

    grouped_audit = audit[audit["status"].eq("grouped")]
    delta_columns = [f"delta_{column}" for column in EXTENSIVE_COLUMNS]
    max_error = (
        float(grouped_audit[delta_columns].abs().to_numpy().max())
        if not grouped_audit.empty
        else 0.0
    )
    if max_error > 1e-6:
        raise ValueError(
            f"CA/CT allocation is not conservative for {year}-{month:02d}: "
            f"max error {max_error}"
        )

    output_path = output_dir / FINAL_FILE_PATTERN.format(year=year, month=month)
    write_excel_atomically(combined, output_path)
    if audit_dir is not None:
        audit_path = audit_dir / f"CC_Plant_Month_{year:04d}_{month:02d}.xlsx"
        write_excel_atomically(audit, audit_path)
    return MonthBuildResult(
        year=year,
        month=month,
        output_rows=len(combined),
        legacy_rows=len(legacy),
        cc_rows=len(cc_rows),
        skipped_cc_plants=int(audit["status"].ne("grouped").sum()),
        max_conservation_error=max_error,
    )


def build_range(
    start: YearMonth,
    end: YearMonth,
    month_agg_dir: Path,
    base_stack_dir: Path,
    generator_dir: Path,
    fuel_cost_file: Path,
    output_dir: Path,
    provenance_dir: Path | None = None,
) -> list[MonthBuildResult]:
    """Build canonical stacks; optionally retain crosswalk and audit artifacts."""
    paths = [month_agg_dir, base_stack_dir, generator_dir]
    for path in paths:
        if not path.is_dir():
            raise FileNotFoundError(f"Required input directory not found: {path}")
    if output_dir.resolve() == base_stack_dir.resolve():
        raise ValueError("Output directory must differ from the ungrouped base")

    fuel_costs = read_monthly_fuel_costs(fuel_cost_file)
    crosswalk_dir = provenance_dir / "crosswalk" if provenance_dir else None
    audit_dir = provenance_dir / "audit" if provenance_dir else None
    crosswalks: dict[int, pd.DataFrame] = {}
    results: list[MonthBuildResult] = []
    requested_months = list(iter_year_months(start, end))
    months_by_year: dict[int, list[int]] = {}
    for year, month in requested_months:
        months_by_year.setdefault(year, []).append(month)

    for year, month in requested_months:
        if year not in crosswalks:
            generator_file = generator_dir / f"3_1_Generator_Y{year}.xlsx"
            plant_ids = collect_cc_plant_ids(month_agg_dir, year, months_by_year[year])
            crosswalk = read_generator_crosswalk(
                generator_file, year, plant_ids=plant_ids
            )
            crosswalks[year] = crosswalk
            if crosswalk_dir is not None:
                output_crosswalk = crosswalk.drop(columns=["source_order"])
                write_excel_atomically(
                    output_crosswalk,
                    crosswalk_dir / f"EIA860_CC_Crosswalk_{year}.xlsx",
                )
        key = (year, month)
        if key not in fuel_costs:
            raise KeyError(f"No monthly natural-gas fuel cost for {year}-{month:02d}")
        results.append(
            build_month(
                year,
                month,
                month_agg_dir,
                base_stack_dir,
                output_dir,
                audit_dir,
                crosswalks[year],
                fuel_costs[key],
            )
        )
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build CA/CT-grouped monthly CAISO natural-gas stacks."
    )
    parser.add_argument("--start", type=parse_year_month, default=DEFAULT_START)
    parser.add_argument("--end", type=parse_year_month, default=DEFAULT_END)
    parser.add_argument("--month-agg-dir", type=Path, default=DEFAULT_MONTH_AGG_DIR)
    parser.add_argument("--base-stack-dir", type=Path, default=DEFAULT_BASE_STACK_DIR)
    parser.add_argument("--generator-dir", type=Path, default=DEFAULT_GENERATOR_DIR)
    parser.add_argument("--fuel-cost-file", type=Path, default=DEFAULT_FUEL_COST_FILE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--provenance-dir",
        type=Path,
        default=None,
        help="Optional directory for EIA-860 crosswalks and monthly audits.",
    )
    return parser


def main() -> None:
    arguments = build_parser().parse_args()
    results = build_range(
        arguments.start,
        arguments.end,
        arguments.month_agg_dir,
        arguments.base_stack_dir,
        arguments.generator_dir,
        arguments.fuel_cost_file,
        arguments.output_dir,
        arguments.provenance_dir,
    )
    print(f"Generated {len(results)} monthly grouped stacks in {arguments.output_dir}")
    print(
        "Rows: "
        f"legacy={sum(item.legacy_rows for item in results)}, "
        f"CC={sum(item.cc_rows for item in results)}, "
        f"skipped CC plant-months={sum(item.skipped_cc_plants for item in results)}"
    )
    print(
        "Maximum extensive-quantity conservation error: "
        f"{max(item.max_conservation_error for item in results):.3e}"
    )


if __name__ == "__main__":
    main()
