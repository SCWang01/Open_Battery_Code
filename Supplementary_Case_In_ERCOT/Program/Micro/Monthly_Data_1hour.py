"""Monthly input contract shared by Benchmark and Rolling (no solver dependency).

Physical slots are keyed by UTC; month/day boundaries use America/Chicago.
Missing five-minute observations are never fabricated. All callers validate the
same complete implementation month plus the next local day before dispatch.
"""

from __future__ import annotations

import json
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

import Experiment_Data_1hour as data

ZONE = "America/Chicago"
UTC_SOURCE = "SCED Timestamp UTC"
UTC_HOUR = "Hour Start UTC"
SLOT = "_UTC Slot"
RESOLVED_CURVES = "Resolved Curves"
CURVE_POLICY = "whole_empty_curve_carry_forward_last_valid"


class CoverageError(ValueError):
    """Insufficient observations to construct every required physical hour."""


def read_monthly_frame(path: Path) -> pd.DataFrame:
    """Read only modeled/audit columns; preserve the literal status code NA."""
    columns = {
        data.SOURCE_TIME,
        data.SCED_TIME,
        UTC_SOURCE,
        data.STATUS,
        "Resource Name",
        "LMP",
        data.TELEMETRY,
        "HSL",
        "LSL",
        "State of Charge",
        "Minimum SOC",
        "Maximum SOC",
        *data.CURVE_COLUMNS,
    }
    return pd.read_excel(
        path,
        sheet_name="Usage",
        keep_default_na=False,
        usecols=lambda column: column in columns,
    )


def normalize_source(raw: pd.DataFrame, name: str) -> pd.DataFrame:
    required = {
        data.SOURCE_TIME,
        data.SCED_TIME,
        UTC_SOURCE,
        data.STATUS,
        "Resource Name",
        "LMP",
        data.TELEMETRY,
        *data.CURVE_COLUMNS,
    }
    if missing := required - set(raw):
        raise ValueError(f"missing columns: {sorted(missing)}")
    raw = raw.copy()
    raw[data.SOURCE_ROW] = np.arange(2, len(raw) + 2)
    if not raw["Resource Name"].astype(str).str.strip().eq(name).all():
        raise ValueError("resource identity does not match filename")
    raw[data.SOURCE_TIME] = pd.to_datetime(
        raw[data.SOURCE_TIME], errors="raise"
    ).dt.round("s")
    raw[data.SCED_TIME] = pd.to_datetime(raw[data.SCED_TIME], errors="raise").dt.round(
        "s"
    )
    raw[SLOT] = (
        pd.to_datetime(raw[UTC_SOURCE], errors="raise", utc=True)
        .dt.round("s")
        .dt.floor("5min")
    )
    local = raw[SLOT].dt.tz_convert(ZONE).dt.tz_localize(None)
    if raw[SLOT].isna().any() or not local.equals(raw[data.SOURCE_TIME]):
        raise ValueError("UTC source slots disagree with local standard timestamps")
    if raw[SLOT].duplicated().any():
        raise ValueError("duplicate five-minute UTC source slots")
    return raw.sort_values(SLOT, kind="stable").reset_index(drop=True)


def _curve(row: dict) -> tuple[tuple[float, float], ...]:
    return tuple(
        (float(row[mw]), float(row[price]))
        for mw, price in data.CURVE_PAIRS
        if pd.notna(row[mw]) and str(row[mw]).strip() != ""
    )


def _historical_curve(name: str, experiment: data.Experiment):
    """Read backward only when a month begins without a usable submission."""
    suffix = experiment.payload["month"].replace("-", "")
    paths = sorted(
        (
            p
            for p in experiment.data_dir.glob(f"{name}_Usage_*.xlsx")
            if p.stem.rsplit("_", 1)[-1] < suffix
        ),
        reverse=True,
    )
    consulted = {}
    for path in paths:
        consulted[str(path.resolve())] = data.sha256(path)
        raw = normalize_source(read_monthly_frame(path), name)
        raw = raw.loc[raw[SLOT].lt(experiment.start.tz_convert("UTC"))]
        valid = raw.loc[data._curve_validity(raw)]
        if not valid.empty:
            row = valid.iloc[-1].to_dict()
            return (
                _curve(row),
                int(row[data.SOURCE_ROW]),
                row[SLOT],
                str(path.resolve()),
            ), consulted
    return None, consulted


def resolve_monthly_curves(raw, hourly, name, path, experiment):
    """Resolve whole curves causally for the implementation month only."""
    valid = data._curve_validity(raw)
    empty = (
        raw[list(data.CURVE_COLUMNS)]
        .astype("string")
        .fillna("")
        .apply(lambda col: col.str.strip())
        .eq("")
        .all(axis=1)
        .to_numpy()
    )
    previous = None
    history_checked = False
    history_hashes = {}
    all_curves, all_origins = [], []
    for hour in hourly.to_dict("records"):
        curves, origins = [], []
        implementation = hour[data.IS_IMPLEMENTATION]
        available = hour[data.AVAILABLE] and implementation
        for index in hour["_Source Indices"]:
            row = raw.iloc[index]
            when = row[SLOT]
            if not implementation:
                continue
            if valid[index]:
                previous = (
                    _curve(row),
                    int(row[data.SOURCE_ROW]),
                    when,
                    str(path.resolve()),
                )
            elif not empty[index] and available:
                raise ValueError(
                    f"{path.name}:{int(row[data.SOURCE_ROW])}: malformed nonempty TPO curve"
                )
            if not available:
                continue
            if previous is None and not history_checked:
                previous, history_hashes = _historical_curve(name, experiment)
                history_checked = True
            if previous is None:
                raise ValueError(
                    f"{path.name}:{int(row[data.SOURCE_ROW])} at {when.tz_convert(ZONE).isoformat()}: empty TPO curve has no earlier valid curve"
                )
            curve, curve_row, curve_time, curve_file = previous
            if curve_time > when:
                raise ValueError("future curve used for dispatch")
            curves.append(curve)
            origins.append(
                {
                    "source_row": int(row[data.SOURCE_ROW]),
                    "time": when.isoformat(),
                    "source_file": str(path.resolve()),
                    "curve_source_row": curve_row,
                    "curve_time": curve_time.isoformat(),
                    "curve_source_file": curve_file,
                    "carried_forward": bool(empty[index]),
                    "age_minutes": int((when - curve_time).total_seconds() / 60),
                }
            )
        all_curves.append(tuple(curves))
        all_origins.append(json.dumps(origins, separators=(",", ":")))
    hourly[RESOLVED_CURVES] = all_curves
    hourly["Curve Sources"] = all_origins
    hourly["Curve Policy"] = CURVE_POLICY
    hourly["Curve Carry Forward Count"] = [
        sum(o["carried_forward"] for o in json.loads(s)) for s in all_origins
    ]
    return history_hashes


def inspect_monthly_member(
    path: Path, experiment: data.Experiment, tail_steps: int = 0,
    *, power_policy: str = data.TELEMETRY_POWER_POLICY,
):
    """Return a shared member or a classified rejection with source provenance."""
    month = experiment.payload["month"]
    suffix = "_Usage_" + month.replace("-", "")
    name = path.stem.removesuffix(suffix)
    audit = {
        "member": name,
        "month": month,
        "source": str(path.resolve()),
        "interval_minutes": 60,
        "valid": False,
        "failure_kind": "invalid_input",
    }
    try:
        if name not in experiment.member_seeds:
            raise ValueError(f"member {name} is not in fixed registry")
        if not path.is_file():
            raise CoverageError("missing monthly source file")
        audit["input_sha256"] = data.sha256(path)
        raw = normalize_source(read_monthly_frame(path), name)
        start = experiment.start.tz_convert("UTC")
        end = experiment.end_exclusive.tz_convert("UTC")
        full_end = (experiment.end_exclusive + pd.DateOffset(days=1)).tz_convert("UTC")
        raw = raw.loc[raw[SLOT].ge(start) & raw[SLOT].lt(full_end)].reset_index(
            drop=True
        )
        expected = pd.date_range(start, full_end, freq="5min", inclusive="left")
        missing = expected.difference(pd.DatetimeIndex(raw[SLOT]))
        audit["missing_bins"] = len(missing)
        audit["missing_slots_utc"] = [t.isoformat() for t in missing]
        expected_days = pd.date_range(
            experiment.start, experiment.end_exclusive, freq="D"
        )
        seen_days = set(raw[SLOT].dt.tz_convert(ZONE).dt.date)
        audit["missing_dates"] = [
            d.date().isoformat() for d in expected_days if d.date() not in seen_days
        ]
        if audit["missing_dates"]:
            raise CoverageError(f"missing full local dates: {audit['missing_dates']}")
        if not pd.DatetimeIndex(raw[SLOT]).isin(expected).all():
            raise ValueError("misaligned source slots")
        for col in (data.TELEMETRY, "LMP"):
            original = raw[col]
            blank = original.isna() | original.astype(str).str.strip().eq("")
            numeric = pd.to_numeric(original, errors="coerce")
            invalid = (
                ~np.isfinite(numeric.to_numpy(dtype=float))
                | original.map(
                    lambda value: isinstance(value, (bool, np.bool_))
                ).to_numpy()
            )
            if np.any(invalid & ~blank.to_numpy()):
                raise ValueError(f"invalid nonblank {col}")
            raw[col] = numeric
            audit[
                (
                    "missing_power_values"
                    if col == data.TELEMETRY
                    else "missing_price_values"
                )
            ] = int(blank.sum())
        raw[data.STATUS] = (
            raw[data.STATUS].fillna("").astype(str).str.strip().str.upper()
        )
        raw["_Hour"] = raw[SLOT].dt.floor("h")
        submitted = data.submitted_power_parameters(
            raw.loc[raw[SLOT].lt(end)], power_policy
        )
        audit["power_limit_policy"] = power_policy
        for col in ("HSL", "LSL", "State of Charge", "Minimum SOC", "Maximum SOC"):
            raw[col] = (
                pd.to_numeric(raw[col], errors="coerce") if col in raw else np.nan
            )
        records = []
        for utc_hour, group in raw.groupby("_Hour", sort=True):
            counts = group[data.STATUS].replace("", "<BLANK>").value_counts().to_dict()
            record = {
                data.STANDARD_TIME: utc_hour.tz_convert(ZONE),
                UTC_HOUR: utc_hour.tz_localize(None),
                "Month": month,
                "Resource Name": name,
                "LMP": float(group["LMP"].mean()),
                data.TELEMETRY: float(group[data.TELEMETRY].mean()),
                data.PARTICIPATING: bool(
                    group[data.STATUS].isin(("ON", "ONTEST")).any()
                ),
                data.STATUS: "|".join(sorted(counts)),
                "Source Status Counts": json.dumps(counts),
                "Source Rows": json.dumps(group[data.SOURCE_ROW].astype(int).tolist()),
                "Source SCED Times": json.dumps(
                    group[data.SCED_TIME].astype(str).tolist()
                ),
                "Source Standard Times": json.dumps(group[SLOT].astype(str).tolist()),
                data.SCED_TIME: group[data.SCED_TIME].iloc[0],
                data.SOURCE_ROW: int(group[data.SOURCE_ROW].iloc[0]),
                "HSL": float(group["HSL"].mean()),
                "LSL": float(group["LSL"].mean()),
                "State of Charge": float(group["State of Charge"].iloc[0]),
                "Observed SOC": float(group["State of Charge"].iloc[0]),
                "Observed Minimum SOC": float(group["Minimum SOC"].min()),
                "Observed Maximum SOC": float(group["Maximum SOC"].max()),
                "Source Record Count": len(group),
                "Power Sample Count": int(group[data.TELEMETRY].count()),
                "Price Sample Count": int(group["LMP"].count()),
                "_Source Indices": group.index.tolist(),
                data.IS_IMPLEMENTATION: utc_hour < end,
            }
            for label, count in (
                ("Source Coverage", "Source Record Count"),
                ("Power Coverage", "Power Sample Count"),
                ("Price Coverage", "Price Sample Count"),
            ):
                record[label] = record[count] / 12
            records.append(record)
        hourly = pd.DataFrame(records)
        if submitted:
            data.add_submitted_hourly_extrema(hourly, raw, "_Hour")
        hours = pd.date_range(start, full_end, freq="h", inclusive="left")
        if hourly.empty or not pd.DatetimeIndex(hourly[UTC_HOUR]).tz_localize(
            "UTC"
        ).equals(hours):
            raise CoverageError("missing complete physical hour")
        if not np.isfinite(hourly[["LMP", data.TELEMETRY]].to_numpy()).all():
            raise CoverageError("hour has no valid price or power samples")
        if tail_steps > 24:
            raise ValueError("monthly files provide at most 24 tail hours")
        hourly[data.ZERO_DAY] = hourly.groupby(hourly[data.STANDARD_TIME].dt.date)[
            data.TELEMETRY
        ].transform(lambda v: bool(v.eq(0).all()))
        implementation = hourly.loc[hourly[data.IS_IMPLEMENTATION]]
        parameters = data.reconstruct_ess(
            implementation[data.TELEMETRY].to_numpy(), experiment.efficiency
        )
        parameters.update(submitted)
        hourly[data.AVAILABLE] = hourly[data.PARTICIPATING] & ~hourly[data.ZERO_DAY]
        hourly[data.EFFECTIVE_HSL] = np.where(
            hourly[data.AVAILABLE], parameters["rated_discharge_mw"], 0.0
        )
        hourly[data.EFFECTIVE_LSL] = np.where(
            hourly[data.AVAILABLE], -parameters["rated_charge_mw"], 0.0
        )
        for col, key in (
            (data.CAPACITY, "capacity_mwh"),
            (data.INITIAL_ENERGY, "initial_energy_mwh"),
            (data.RATED_CHARGE, "rated_charge_mw"),
            (data.RATED_DISCHARGE, "rated_discharge_mw"),
        ):
            hourly[col] = parameters[key]
        audit["history_input_sha256"] = resolve_monthly_curves(
            raw, hourly, name, path, experiment
        )
        audit.update(parameters)
        audit.update(
            valid=True,
            reason="valid",
            failure_kind=None,
            implementation_steps=experiment.steps,
            validated_tail_hours=24,
            source_rows=len(raw),
            participating_hours=int(
                hourly[data.PARTICIPATING].iloc[: experiment.steps].sum()
            ),
            trading_hours=int(hourly[data.AVAILABLE].iloc[: experiment.steps].sum()),
            carried_forward_source_curves=int(
                hourly["Curve Carry Forward Count"].sum()
            ),
            min_source_records_per_hour=int(hourly["Source Record Count"].min()),
        )
        frame = (
            hourly.iloc[: experiment.steps + tail_steps]
            .drop(columns="_Source Indices")
            .copy()
        )
        frame.attrs["ess_parameters"] = parameters
        prices = implementation["LMP"]
        return (
            data.MemberData(
                name,
                path,
                frame,
                0.0,
                parameters["capacity_mwh"],
                parameters["initial_energy_mwh"],
                float(prices.min()),
                float(prices.max()),
                audit,
            ),
            audit,
        )
    except (ValueError, KeyError, TypeError) as exc:
        audit.update(
            reason=str(exc),
            failure_kind=(
                "coverage" if isinstance(exc, CoverageError) else "invalid_input"
            ),
        )
        return None, audit


def write_quality(experiment, audits):
    """Both algorithms write the same contract report regardless of tail use."""
    folder = experiment.output_root / "reports"
    data.atomic_json(
        folder / experiment.output_name("monthly_input_quality_1hour.json"), audits
    )
    pd.DataFrame(
        [
            {
                k: json.dumps(v) if isinstance(v, (dict, list)) else v
                for k, v in a.items()
            }
            for a in audits
        ]
    ).to_csv(
        folder / experiment.output_name("monthly_input_quality_1hour.csv"), index=False
    )


def load_monthly_members(
    experiment, requested: Sequence[str] | None, *, tail_steps=0, workers=12,
    power_policy=data.TELEMETRY_POWER_POLICY,
):
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError("workers must be a positive integer")
    names = data.selected_names(experiment, requested)
    suffix = experiment.payload["month"].replace("-", "")
    resources, audits = {}, {}
    with ProcessPoolExecutor(
        max_workers=min(workers, len(names)), mp_context=mp.get_context("spawn")
    ) as pool:
        futures = {
            pool.submit(
                inspect_monthly_member,
                experiment.data_dir / f"{name}_Usage_{suffix}.xlsx",
                experiment,
                tail_steps,
                power_policy=power_policy,
            ): name
            for name in names
        }
        for future in as_completed(futures):
            member, audit = future.result()
            audits[audit["member"]] = audit
            if member is not None:
                resources[member.name] = member
            print(
                f"Validated {suffix} {audit['member']}: {audit['reason']}", flush=True
            )
    write_quality(experiment, [audits[n] for n in names])
    invalid = [n for n in names if audits[n]["failure_kind"] == "invalid_input"]
    if invalid:
        raise ValueError(
            f"invalid monthly inputs: {invalid}; see {experiment.output_root / 'reports'}"
        )
    if not resources:
        raise ValueError("no valid member months remain after coverage exclusions")
    return tuple(resources[n] for n in names if n in resources)


def write_batch_summary(
    config_path, months, method, *, validate_only=False, results_root=None
):
    """Assemble only the selected months, never mix in stale historical outputs."""
    experiments = [data.load_experiment(config_path, m) for m in months]
    root = (
        Path(results_root) if results_root is not None else experiments[0].output_root
    )
    audits, summaries = [], []
    for experiment in experiments:
        report = (
            experiment.output_root
            / "reports"
            / experiment.output_name("monthly_input_quality_1hour.json")
        )
        if report.exists():
            audits.extend(json.loads(report.read_text(encoding="utf-8")))
        if not validate_only:
            folder = root / method
            summary = pd.read_csv(
                folder / experiment.output_name("member_summary_1hour.csv")
            )
            summary.insert(0, "month", experiment.payload["month"])
            summaries.append(summary)
    reports = root / "reports"
    data.atomic_json(reports / f"{method}_monthly_validation_1hour.json", audits)
    code_root = Path(__file__).resolve().parent
    data.atomic_json(
        reports / f"{method}_validation_manifest_1hour.json",
        {
            "status": (
                "failed"
                if any(
                    not a["valid"] and a.get("failure_kind") != "coverage"
                    for a in audits
                )
                else "complete"
            ),
            "months": list(months),
            "checked_member_months": len(audits),
            "valid_member_months": sum(a["valid"] for a in audits),
            "excluded_coverage": sum(
                a.get("failure_kind") == "coverage" for a in audits
            ),
            "invalid_inputs": [
                {"month": a["month"], "member": a["member"], "reason": a["reason"]}
                for a in audits
                if not a["valid"] and a.get("failure_kind") != "coverage"
            ],
            "config_sha256": data.sha256(Path(config_path)),
            "code_sha256": {
                name: data.sha256(code_root / name)
                for name in (
                    "project_paths.py",
                    "Experiment_Data_1hour.py",
                    "Monthly_Data_1hour.py",
                    "Benchmark_1hour.py",
                    "Rolling_Window_1hour.py",
                    "distributed_Rolling_Window_1hour.py",
                )
            },
            "simulation_performed": not validate_only,
        },
    )
    if audits:
        columns = [
            "month",
            "member",
            "valid",
            "failure_kind",
            "reason",
            "implementation_steps",
            "missing_bins",
            "missing_power_values",
            "missing_price_values",
            "capacity_mwh",
            "rated_charge_mw",
            "rated_discharge_mw",
            "initial_energy_mwh",
            "trading_hours",
            "carried_forward_source_curves",
            "input_sha256",
        ]
        pd.DataFrame(audits).reindex(columns=columns).to_csv(
            reports / f"{method}_monthly_validation_1hour.csv", index=False
        )
    if summaries:
        pd.concat(summaries, ignore_index=True).to_csv(
            reports / f"{method}_monthly_summary_1hour.csv", index=False
        )
    return audits
