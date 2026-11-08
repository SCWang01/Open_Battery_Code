"""Compute signed normalized gaps between each resource's telemetry power and
rated endpoints or category power points from dsfunction.

Run ``python Realized_Match.py`` to read *_1hour_YYYYMM.xlsx files from
Results/Micro_Results/rolling and Results/Micro_Results/realized_profit. Match exactly by resource,
month, and Hour Start UTC; write Realized_Match_{resource}_1hour_YYYYMM.xlsx to
Results/Micro_Results/realized_match. Do not match using SCED timestamps with offsets in
seconds or by row number. Input files remain unchanged.

The first ten output columns are Time, Telemetered Net Output, MC, CC, CD, NU,
DC, DD, MD, and Notes.
Append Hour Start UTC, Resource Name, Month, and Original Telemetered Net Output.
Time is the local hour in Chicago; UTC identifies the physical hour. The final
column preserves the original hourly mean power (MW). The second column contains
telemetry x clipped to rolling's [Effective LSL, Effective HSL], with its original
value recorded in the clipping note. Fall back to [Pcmax, Pdmax] for older files
missing both effective bounds. Original HSL/LSL are telemetry fields only and do
not constrain Rolling bids based on fixed rated power. Reuse classify_p_ess with
power from the third dsfunction column and the row's Pcmax/Pdmax.

Calculation conventions (preserve signs; smaller absolute gaps mean closer matches,
not category probabilities):
* MC always uses the current row's Pcmax and MD its Pdmax; dsfunction need not
  provide these endpoints. For every category power point p, compute
  (x-p)/(Effective HSL-Effective LSL) and write it to the corresponding category
  column; leave absent categories blank. Do not search for left/right power
  segments. An exact hit still requires computing gaps for all other categories.
* If a category has multiple distinct power points, retain the signed gap with
  the smallest magnitude; break ties using the lower power point. Record all
  candidate power/gap pairs and the selected power in the notes, keeping the
  ten-column numerical output suitable for statistical analysis.
* Even a single category uses its actual power point, not the nearest power bound.
  Use the same formula when telemetry lies outside the curve's power-point range;
  extrapolation is unnecessary.
* If dsfunction is missing (including an absent column), invalid, unclassifiable,
  or assigns conflicting categories to the same power, retain MC/MD, leave other
  category gaps blank, and add a note. Leave all gaps blank for zero-width physical
  ranges, invalid telemetry/bounds/rated power, or missing matches. Compute both
  endpoints even if one of Pcmax/Pdmax is zero, using the original curve classification
  priority.
* Preserve the union of both input timelines and duplicate records. For duplicate
  times, leave gaps blank and add notes rather than choosing a row arbitrarily.
  Treat an entirely missing resource-month file as a missing match. Isolate and
  report file-structure errors at the resource-month level.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

from classify_p_ess import (
    REL_TOL,
    as_float,
    classify_p_ess,
    find_column,
    parse_dsfunction,
    save_workbook_atomically,
    validate_resource_name,
    values_match,
)


from project_paths import PROJECT_ROOT, MICRO_RESULTS_DIR
DEFAULT_ROLLING_DIR = MICRO_RESULTS_DIR / "rolling"
DEFAULT_REALIZED_DIR = MICRO_RESULTS_DIR / "realized_profit"
DEFAULT_OUTPUT_DIR = MICRO_RESULTS_DIR / "realized_match"
STATES = ("MC", "CC", "CD", "NU", "DC", "DD", "MD")
TIME_HEADER = "Hour Start UTC"
TIMEZONE = "America/Chicago"
POWER_HEADER = "Telemetered Net Output"
ORIGINAL_POWER_HEADER = "Original Telemetered Net Output"
NOTE_HEADER = "Notes"
OUTPUT_HEADERS = (
    "Time", POWER_HEADER, *STATES, NOTE_HEADER,
    TIME_HEADER, "Resource Name", "Month", ORIGINAL_POWER_HEADER,
)
MONTH_PATTERN = re.compile(r"[0-9]{4}-(?:0[1-9]|1[0-2])")
ROLLING_FILENAME = re.compile(
    r"(?P<resource>.+)_Rolling_Window_1hour_(?P<month>[0-9]{6})\.xlsx"
)
REALIZED_FILENAME = re.compile(
    r"Realized_Profit_(?P<resource>.+)_1hour_(?P<month>[0-9]{6})\.xlsx"
)
ROLLING_FIELDS = ("Pcmax", "Pdmax")
EFFECTIVE_LSL_HEADER = "Effective LSL"
EFFECTIVE_HSL_HEADER = "Effective HSL"
# Input-bound validation is distinct from the classifier's 1 kW label band.
INPUT_POWER_BOUND_ABS_TOL = 5e-4


@dataclass(frozen=True)
class SourceRow:
    """One input row retaining its original Excel row for diagnostics."""

    excel_row: int
    values: dict[str, Any]


@dataclass(frozen=True)
class MatchResult:
    """Clipped power, signed category gaps (legacy distances field), and row notes."""

    power: float | None
    distances: dict[str, float]
    notes: tuple[str, ...]


def _number(value: Any, name: str, excel_row: int) -> float:
    if isinstance(value, bool):
        raise ValueError(f"Row {excel_row}: {name} must not be boolean")
    return as_float(value, name=name, excel_row=excel_row)


def _timestamp(value: Any, excel_row: int) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip())
        except ValueError as exc:
            raise ValueError(
                f"Row {excel_row}: invalid UTC hour {value!r}"
            ) from exc
    if not isinstance(value, datetime) or value.tzinfo is not None:
        raise ValueError(f"Row {excel_row}: expected a timezone-free UTC datetime, got {value!r}")
    if value.minute or value.second or value.microsecond:
        raise ValueError(
            f"Row {excel_row}: timestamp is not on the hourly grid: {value}"
        )
    return value


def _month(value: str) -> str:
    if not MONTH_PATTERN.fullmatch(value) or value.startswith("0000-"):
        raise ValueError(f"Invalid month {value!r}; expected YYYY-MM")
    return value


def _file_identity(path: Path, *, rolling: bool) -> tuple[str, str]:
    pattern = ROLLING_FILENAME if rolling else REALIZED_FILENAME
    match = pattern.fullmatch(path.name)
    if match is None:
        raise ValueError(f"Invalid monthly hourly filename: {path.name}")
    resource = validate_resource_name(match["resource"], input_path=path)
    token = match["month"]
    return resource, _month(f"{token[:4]}-{token[4:]}")


def _local_time(utc_hour: datetime) -> datetime:
    return (
        pd.Timestamp(utc_hour).tz_localize("UTC").tz_convert(TIMEZONE)
        .tz_localize(None).to_pydatetime()
    )


def read_source(
    path: Path, resource: str, *, rolling: bool, month: str
) -> dict[datetime, list[SourceRow]]:
    """Read one member-month keyed by UTC hour, retaining duplicate records."""
    if _file_identity(path, rolling=rolling) != (resource, month):
        raise ValueError(f"{path.name}: filename does not match {resource}, {month}")
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        rows = iter(workbook.active.iter_rows(values_only=True))
        headers = list(next(rows, ()))
        fields = ROLLING_FIELDS if rolling else (POWER_HEADER,)
        required = (TIME_HEADER, "Resource Name", "Month", *fields)
        for header in required:
            find_column(headers, header)
        if rolling and "dsfunction" in headers:
            find_column(headers, "dsfunction")
        if rolling and (
            EFFECTIVE_LSL_HEADER in headers or EFFECTIVE_HSL_HEADER in headers
        ):
            # A partial pair is malformed, not a legacy file eligible for fallback.
            find_column(headers, EFFECTIVE_LSL_HEADER)
            find_column(headers, EFFECTIVE_HSL_HEADER)
        result: dict[datetime, list[SourceRow]] = defaultdict(list)
        for excel_row, values in enumerate(rows, start=2):
            if all(value is None for value in values):
                continue
            row = dict(zip(headers, values))
            name = validate_resource_name(row["Resource Name"], input_path=path)
            if name != resource:
                raise ValueError(
                    f"{path.name}, row {excel_row}: resource {name!r} != {resource!r}"
                )
            if row["Month"] != month:
                raise ValueError(
                    f"{path.name}, row {excel_row}: Month={row['Month']!r} != {month!r}"
                )
            timestamp = _timestamp(row[TIME_HEADER], excel_row)
            if _local_time(timestamp).strftime("%Y-%m") != month:
                raise ValueError(
                    f"{path.name}, row {excel_row}: UTC hour {timestamp} "
                    f"is outside local month {month}"
                )
            result[timestamp].append(SourceRow(excel_row, row))
        if not result:
            raise ValueError(f"{path.name}: no data rows")
        return dict(result)
    finally:
        workbook.close()


def _power_points(
    dsfunction: Any,
    *,
    pcmax: float,
    pdmax: float,
    lsl: float,
    hsl: float,
    excel_row: int,
    notes: list[str],
) -> list[tuple[float, str]]:
    segments = parse_dsfunction(dsfunction, excel_row)
    points = []
    for segment in segments:
        power = _number(segment[2], "dsfunction power", excel_row)
        if (
            power < lsl
            and not math.isclose(power, lsl, rel_tol=REL_TOL, abs_tol=INPUT_POWER_BOUND_ABS_TOL)
        ) or (
            power > hsl
            and not math.isclose(power, hsl, rel_tol=REL_TOL, abs_tol=INPUT_POWER_BOUND_ABS_TOL)
        ):
            raise ValueError(
                f"DS_POWER_OUT_OF_BOUNDS: {power:g} is outside [{lsl:g}, {hsl:g}]"
            )
        state = classify_p_ess(power, segment, excel_row, pcmax=pcmax, pdmax=pdmax)
        if state not in STATES:
            raise ValueError(f"UNKNOWN_CATEGORY: power={power:g}, state={state}")
        points.append((power, state))
    if any(left[0] > right[0] for left, right in zip(points, points[1:])):
        notes.append("UNSORTED_POWER: power points sorted in ascending order")
    points.sort()
    unique: list[tuple[float, str]] = []
    duplicates = 0
    for power, state in points:
        if unique and values_match(power, unique[-1][0]):
            if state != unique[-1][1]:
                raise ValueError(f"CONFLICTING_CATEGORY: power {power:g} has conflicting categories")
            duplicates += 1
        else:
            unique.append((power, state))
    if duplicates:
        notes.append(f"DUPLICATE_POWER: merged {duplicates} duplicate power points with the same category")
    return unique


def calculate_relative_distances(
    telemetry: Any,
    dsfunction: Any,
    *,
    lsl: Any,
    hsl: Any,
    pcmax: Any,
    pdmax: Any,
    excel_row: int = 2,
) -> MatchResult:
    """Compute signed gaps using the supplied effective lower/upper power bounds.

    The public name and ``distances`` field are retained for existing callers.
    ``lsl``/``hsl`` are Rolling's effective bounds, not raw telemetry limits.
    MC/MD always use Pcmax/Pdmax after shared input validation. Invalid curves
    leave only the other categories blank, with a diagnostic note.
    """
    notes: list[str] = []
    power: float | None = None
    try:
        power = _number(telemetry, POWER_HEADER, excel_row)
        lower = _number(lsl, EFFECTIVE_LSL_HEADER, excel_row)
        upper = _number(hsl, EFFECTIVE_HSL_HEADER, excel_row)
        if lower > upper:
            raise ValueError(f"INVALID_LIMITS: Effective LSL={lower:g} > Effective HSL={upper:g}")
        original = power
        power = min(max(power, lower), upper)
        if original != power:
            boundary = "LSL" if original < lower else "HSL"
            notes.append(f"CLIPPED_{boundary}: clipped to effective bounds, original telemetry={original:g}, value used={power:g}")
        span = upper - lower
        if not math.isfinite(span):
            raise ValueError("INVALID_LIMIT_SPAN: power range is not finite")
        if values_match(lower, upper):
            notes.append("ZERO_POWER_RANGE: Effective HSL=Effective LSL (within tolerance); normalized gap is undefined")
            return MatchResult(power, {}, tuple(notes))
        charge = _number(pcmax, "Pcmax", excel_row)
        discharge = _number(pdmax, "Pdmax", excel_row)
        if (
            charge > 0
            or discharge < 0
            or charge < lower - INPUT_POWER_BOUND_ABS_TOL
            or discharge > upper + INPUT_POWER_BOUND_ABS_TOL
        ):
            raise ValueError("INVALID_RATE_LIMITS: requires Effective LSL<=Pcmax<=0<=Pdmax<=Effective HSL")
        if values_match(charge, 0) or values_match(discharge, 0):
            notes.append("ZERO_RATE_LIMIT: MC/MD use rated endpoints; curve uses the original classification priority")
        endpoint_distances = {
            "MC": (power - charge) / span,
            "MD": (power - discharge) / span,
        }
        if not all(math.isfinite(gap) for gap in endpoint_distances.values()):
            raise ValueError("NONFINITE_GAP: normalized gap is not finite")
    except (ValueError, TypeError, OverflowError) as exc:
        notes.append(f"INVALID_ROW: {exc}")
        return MatchResult(power, {}, tuple(notes))

    # Curve failures must not erase the independently validated rated endpoints.
    try:
        points = _power_points(
            dsfunction,
            pcmax=charge,
            pdmax=discharge,
            lsl=lower,
            hsl=upper,
            excel_row=excel_row,
            notes=notes,
        )
        candidates: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for candidate, state in points:
            if state in endpoint_distances:
                continue  # Rated endpoints are authoritative even for rounded curve points.
            gap = (power - candidate) / span
            if not math.isfinite(gap):
                raise ValueError("NONFINITE_GAP: normalized gap is not finite")
            candidates[state].append((candidate, gap))
        distances = endpoint_distances.copy()
        for state, options in candidates.items():
            selected_power, gap = min(options, key=lambda item: (abs(item[1]), item[0]))
            distances[state] = gap
            if len(options) > 1:
                detail = ", ".join(f"{p:g}/{value:.12g}" for p, value in options)
                notes.append(
                    f"MULTIPLE_CATEGORY_POINTS: {state}; candidate power/gap pairs=[{detail}]; "
                    f"selected power={selected_power:g} (nearest; ties use lower power)"
                )
        return MatchResult(power, distances, tuple(notes))
    except (ValueError, TypeError, OverflowError) as exc:
        notes.append(f"INVALID_DSFUNCTION: {exc}; retaining gaps to the MC/MD rated endpoints")
        return MatchResult(power, endpoint_distances, tuple(notes))


def _unmatched_result(telemetry: SourceRow | None, notes: list[str]) -> MatchResult:
    power = None
    if telemetry is not None:
        try:
            power = _number(
                telemetry.values[POWER_HEADER], POWER_HEADER, telemetry.excel_row
            )
            notes.append("UNCLIPPED_TELEMETRY: no unique valid pairing; retaining original telemetry")
        except ValueError as exc:
            notes.append(f"INVALID_TELEMETRY: {exc}")
    return MatchResult(power, {}, tuple(notes))


def match_resource(
    resource: str,
    rolling_path: Path | None,
    realized_path: Path | None,
    output_path: Path,
    *,
    month: str,
) -> Counter:
    """Match monthly UTC timelines and export clipped and original mean power."""
    month = _month(month)
    for source in (rolling_path, realized_path):
        if source is not None and source.resolve() == output_path.resolve():
            raise ValueError("Output must differ from input workbooks")
    rolling = (
        read_source(rolling_path, resource, rolling=True, month=month)
        if rolling_path else {}
    )
    realized = (
        read_source(realized_path, resource, rolling=False, month=month)
        if realized_path else {}
    )
    if not rolling and not realized:
        raise ValueError(f"{resource}: both sources are missing")
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Realized Match"
    worksheet.append(OUTPUT_HEADERS)
    counts: Counter = Counter()
    for timestamp in sorted(rolling.keys() | realized.keys()):
        models, measurements = rolling.get(timestamp, []), realized.get(timestamp, [])
        notes = []
        if not models:
            notes.append("MISSING_ROLLING: matching model data missing")
        if not measurements:
            notes.append("MISSING_TELEMETRY: matching telemetry data missing")
        if len(models) > 1:
            notes.append(f"DUPLICATE_ROLLING_TIME: {len(models)} rows; no unique match")
        if len(measurements) > 1:
            notes.append(
                f"DUPLICATE_TELEMETRY_TIME: {len(measurements)} rows; no unique match"
            )
        # No Cartesian join: each source occurrence is retained without inventing matches.
        for occurrence in range(max(len(models), len(measurements))):
            measurement = (
                measurements[occurrence] if occurrence < len(measurements) else None
            )
            original_power = None
            if measurement is not None:
                try:
                    original_power = _number(
                        measurement.values[POWER_HEADER], POWER_HEADER, measurement.excel_row
                    )
                except (ValueError, TypeError, OverflowError):
                    pass  # The matching branch below records the invalid telemetry.
            if notes:
                result = _unmatched_result(measurement, notes.copy())
            else:
                model = models[0]
                values = model.values
                result = calculate_relative_distances(
                    measurements[0].values[POWER_HEADER],
                    values.get("dsfunction"),
                    lsl=values.get(EFFECTIVE_LSL_HEADER, values["Pcmax"]),
                    hsl=values.get(EFFECTIVE_HSL_HEADER, values["Pdmax"]),
                    pcmax=values["Pcmax"],
                    pdmax=values["Pdmax"],
                    excel_row=model.excel_row,
                )
            worksheet.append(
                (
                    _local_time(timestamp),
                    result.power,
                    *(result.distances.get(state) for state in STATES),
                    "; ".join(result.notes) or None,
                    timestamp,
                    resource,
                    month,
                    original_power,
                )
            )
            counts["rows"] += 1
            counts["calculated" if result.distances else "blank_distance"] += 1
            if result.notes:
                counts["annotated"] += 1
            for note in result.notes:
                counts[note.split(":", 1)[0]] += 1
    worksheet.freeze_panes = "C2"
    worksheet.auto_filter.ref = worksheet.dimensions
    for cell in worksheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="203864")
    worksheet.column_dimensions["A"].width = 23
    worksheet.column_dimensions["B"].width = 28
    for column in "CDEFGHI":
        worksheet.column_dimensions[column].width = 13
    worksheet.column_dimensions["J"].width = 85
    worksheet.column_dimensions["K"].width = 23
    worksheet.column_dimensions["L"].width = 27
    worksheet.column_dimensions["M"].width = 12
    worksheet.column_dimensions["N"].width = 38
    for row in worksheet.iter_rows(min_row=2):
        row[0].number_format = "yyyy-mm-dd hh:mm"
        row[10].number_format = "yyyy-mm-dd hh:mm"
        row[13].number_format = "0.000000"
        for cell in row[1:9]:
            cell.number_format = "0.000000"
        row[9].alignment = Alignment(wrap_text=True, vertical="top")
        if row[9].value:
            worksheet.row_dimensions[row[9].row].height = 48
    try:
        save_workbook_atomically(workbook, output_path)
    finally:
        workbook.close()
    return counts


def _discover(directory: Path, *, rolling: bool) -> dict[tuple[str, str], Path]:
    if not directory.is_dir():
        raise NotADirectoryError(f"Input directory not found: {directory}")
    pattern = ROLLING_FILENAME if rolling else REALIZED_FILENAME
    result = {}
    claimed = set()
    for path in sorted(directory.glob("*_1hour_??????.xlsx")):
        if not path.is_file() or path.name.startswith("~$") or not pattern.fullmatch(path.name):
            continue
        resource, month = _file_identity(path, rolling=rolling)
        identity = (resource.casefold(), month)
        if identity in claimed:
            raise ValueError(f"Duplicate member-month input: {resource}, {month}")
        claimed.add(identity)
        result[resource, month] = path
    return result


def main(argv: list[str] | None = None) -> int:
    """Batch member-month pairs, optionally selecting resources and one month."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--rolling-dir", type=Path, default=DEFAULT_ROLLING_DIR)
    parser.add_argument("--realized-dir", type=Path, default=DEFAULT_REALIZED_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--resource", nargs="+", help="Optional resource names")
    parser.add_argument("--month", type=_month, help="YYYY-MM; omit for all discovered months")
    args = parser.parse_args(argv)
    if args.output_dir.resolve() in {
        args.rolling_dir.resolve(),
        args.realized_dir.resolve(),
    }:
        parser.error("output directory must differ from both input directories")
    rolling = _discover(args.rolling_dir, rolling=True)
    realized = _discover(args.realized_dir, rolling=False)
    identities = rolling.keys() | realized.keys()
    if args.resource:
        unknown = set(args.resource) - {resource for resource, _ in identities}
        if unknown:
            parser.error(f"no input workbooks for resources: {sorted(unknown)}")
        identities = {key for key in identities if key[0] in args.resource}
    if args.month:
        identities = {key for key in identities if key[1] == args.month}
    if not identities:
        parser.error("no monthly hourly input workbooks found for the selection")
    failures = 0
    totals: Counter = Counter()
    claimed_outputs = set()
    for resource, month in sorted(identities):
        try:
            validate_resource_name(resource, input_path=args.output_dir)
            output = args.output_dir / (
                f"Realized_Match_{resource}_1hour_{month.replace('-', '')}.xlsx"
            )
            output_key = str(output.resolve()).casefold()
            if output_key in claimed_outputs:
                raise ValueError(f"Output collision: {output.name}")
            claimed_outputs.add(output_key)
            counts = match_resource(
                resource, rolling.get((resource, month)), realized.get((resource, month)),
                output, month=month,
            )
            totals.update(counts)
            print(f"Done [{resource}, {month}]: {dict(counts)}", flush=True)
        except Exception as exc:
            failures += 1
            print(f"Failed [{resource}, {month}]: {exc}", file=sys.stderr, flush=True)
    print(
        f"Batch complete: {len(identities) - failures} succeeded, {failures} failed. {dict(totals)}"
    )
    print(f"Output: {args.output_dir.resolve()}")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        raise SystemExit(f"Error: {exc}") from None
