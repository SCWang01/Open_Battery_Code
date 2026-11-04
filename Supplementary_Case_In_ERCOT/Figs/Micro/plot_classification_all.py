"""Plot every configured hourly ESR and their pooled resource-hour counts.

Run with Python from any directory; optional --experiment and --output-dir
override the repository defaults. Inputs are read once per workbook, validated
before export, and never changed. Category semantics and figure styling come
from the preserved single-resource example. Leftward Rolling and telemetry-hit
bars overlay the telemetry total count profile; the right deviations are preserved.
No optimization is rerun.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import to_rgba
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import MaxNLocator
import numpy as np
import pandas as pd
from scipy.interpolate import PchipInterpolator

from Classification_Test import plot_classification_example as example


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / 'Program/Micro/experiment_config_1hour.json'
DEFAULT_OUTPUT = ROOT / 'Figs/Micro/Classification_All'
STATES = example.STATES
ROLLING_BAR_HEIGHT = 0.36
PROFILE_ALPHA = 0.28
KEYS = ["Resource Name", "Hour Start UTC"]
COUNT_COLUMNS = ["Model Hours", "Telemetry Hit Hours", "Telemetry Outside Hours"]
STATUSES = ("Hit", "Outside threshold", "Excluded")
NOTE_COLUMN = "Notes"


@dataclass
class ResourceResult:
    """One resource's monthly counts, full plotting records, and audit."""

    name: str
    counts: pd.DataFrame
    monthly_counts: pd.DataFrame
    points: pd.DataFrame
    audit: dict


def input_contract(config_path: Path) -> tuple[dict, Path, list[tuple]]:
    """Validate configured selection and all expected workbook paths up front."""
    config = json.loads(config_path.read_text(encoding="utf-8"))
    members, months = config.get("members"), config.get("months")
    if (
        config.get("schema_version") != 3
        or not isinstance(members, dict)
        or not members
        or not isinstance(months, list)
        or not months
    ):
        raise ValueError("Expected a schema-3 configuration with members and months")
    if any(not re.fullmatch(r"[A-Za-z0-9_]+", name) for name in members):
        raise ValueError("Invalid configured resource name")
    if "ALL_ESR" in members:
        raise ValueError("ALL_ESR is reserved for the aggregate output")
    if any(
        not isinstance(month, str)
        or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month)
        for month in months
    ) or len(months) != len(set(months)):
        raise ValueError("Months must be unique YYYY-MM strings")
    config["months"] = sorted(months)
    results_root = (config_path.parent / config["output_root"]).resolve()
    paths = []
    for name in members:
        for month in config["months"]:
            suffix = month.replace("-", "")
            paths.append((
                name, month,
                results_root / "Classified" / f"classified_{name}_1hour_{suffix}.xlsx",
                results_root / "realized_match" / f"Realized_Match_{name}_1hour_{suffix}.xlsx",
            ))
    missing = [str(path) for _, _, *pair in paths for path in pair if not path.is_file()]
    if missing:
        raise ValueError("Missing expected workbooks:\n" + "\n".join(missing))
    return config, results_root, paths


def validate_frame(frame: pd.DataFrame, name: str, month: str, kind: str) -> pd.DataFrame:
    """Check identity and local month; retain matched missing hours for exclusion."""
    required = set(KEYS + ["Month"])
    required.update(["P_ESS_state"] if kind == "classified" else [
        "Telemetered Net Output", NOTE_COLUMN, *STATES,
    ])
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"{name}/{month}/{kind}: missing columns {sorted(missing)}")
    if frame.empty:
        raise ValueError(f"{name}/{month}/{kind}: empty workbook")
    frame = frame.copy()
    if not frame["Resource Name"].eq(name).all() or not frame["Month"].eq(month).all():
        raise ValueError(f"{name}/{month}/{kind}: resource or month identity mismatch")
    try:
        times = pd.to_datetime(frame["Hour Start UTC"], errors="raise", utc=True)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name}/{month}/{kind}: invalid UTC timestamps") from exc
    local_months = times.dt.tz_convert("America/Chicago").dt.strftime("%Y-%m")
    if (times.notna() & local_months.ne(month)).any():
        raise ValueError(f"{name}/{month}/{kind}: UTC hour outside local month")
    if (times.notna() & times.ne(times.dt.floor("h"))).any():
        raise ValueError(f"{name}/{month}/{kind}: timestamp is not an exact UTC hour")
    if kind == "classified" and (times.isna().any() or times.duplicated().any()):
        raise ValueError(f"{name}/{month}/{kind}: missing or duplicated UTC hour")
    frame["Hour Start UTC"] = times.dt.tz_localize(None)
    return frame


def display_positions(points: pd.DataFrame) -> pd.DataFrame:
    """Deterministically jitter categories only; never change signed deviations."""
    points = points.sort_values(KEYS, kind="stable", na_position="last").reset_index(drop=True).copy()
    points["Display X"] = points["Nearest Gap"]
    points["Display Y"] = np.nan
    for index, state in enumerate(STATES):
        selected = points.index[points["Nearest Category"].eq(state)]
        offsets = (
            (np.arange(len(selected)) * 0.6180339887498949) % 1 - 0.5
        ) * example.VERTICAL_JITTER
        points.loc[selected, "Display Y"] = index + offsets
    points["Beyond Display Range"] = points["Nearest Gap"].abs().gt(example.AXIS_LIMIT)
    return points


def classify_points(matched: pd.DataFrame) -> pd.DataFrame:
    """Apply the example's exact exclusion, nearest-category, and hit rules."""
    matched = matched.sort_values(KEYS, kind="stable").reset_index(drop=True).copy()
    matched["Nearest Category"] = ""
    matched["Nearest Gap"] = np.nan
    matched["Plot Status"] = "Excluded"
    matched["Exclusion Reason"] = ""
    matched["Tied Categories"] = ""
    duplicate = matched.duplicated(KEYS, keep=False)
    for index, row in matched.iterrows():
        note = str(row.get(NOTE_COLUMN, ""))
        reason = ""
        if pd.isna(row["Hour Start UTC"]) or duplicate.iloc[index]:
            reason = "Missing or duplicate UTC hour"
        elif "ZERO_POWER_RANGE" in note:
            reason = "Zero effective power range"
        elif any(tag in note for tag in (
            "MISSING_ROLLING", "MISSING_TELEMETRY",
            "DUPLICATE_ROLLING_TIME", "DUPLICATE_TELEMETRY_TIME",
        )):
            reason = "No unique source pairing"
        gaps = {}
        if not reason:
            try:
                power = float(row["Telemetered Net Output"])
                if isinstance(row["Telemetered Net Output"], (bool, np.bool_)) or not np.isfinite(power):
                    raise ValueError("Invalid matched telemetry power")
                for state in STATES:
                    if pd.isna(row[state]):
                        continue
                    value = float(row[state])
                    if isinstance(row[state], (bool, np.bool_)) or not np.isfinite(value):
                        raise ValueError("Invalid category gap")
                    gaps[state] = value
                if not gaps:
                    raise ValueError("No valid category gap")
            except (ValueError, TypeError, OverflowError) as exc:
                reason = str(exc)
        if reason:
            matched.at[index, "Exclusion Reason"] = reason
            continue
        nearest, candidates = example.nearest_category(gaps, power)
        matched.at[index, "Nearest Category"] = nearest
        matched.at[index, "Nearest Gap"] = gaps[nearest]
        matched.at[index, "Plot Status"] = (
            "Hit" if abs(gaps[nearest]) < example.THRESHOLD else "Outside threshold"
        )
        if len(candidates) > 1:
            matched.at[index, "Tied Categories"] = "|".join(candidates)
    return display_positions(matched)


def category_counts(classified: pd.DataFrame, points: pd.DataFrame) -> pd.DataFrame:
    """Count the separate model and telemetry populations in canonical order."""
    return pd.DataFrame({
        "Category": STATES,
        "Model Hours": [int(classified["P_ESS_state"].eq(s).sum()) for s in STATES],
        "Telemetry Hit Hours": [
            int((points["Nearest Category"].eq(s) & points["Plot Status"].eq("Hit")).sum())
            for s in STATES
        ],
        "Telemetry Outside Hours": [
            int((points["Nearest Category"].eq(s) & points["Plot Status"].eq("Outside threshold")).sum())
            for s in STATES
        ],
    })


def summarize(classified: pd.DataFrame, points: pd.DataFrame) -> dict:
    """Reconcile both populations and expose exclusions instead of dropping them."""
    counts = category_counts(classified, points)
    status = {s: int(points["Plot Status"].eq(s).sum()) for s in STATUSES}
    audit = {
        "model_rows": len(classified),
        "model_classified_hours": int(counts["Model Hours"].sum()),
        "model_unclassified_hours": int((~classified["P_ESS_state"].isin(STATES)).sum()),
        "telemetry_rows": len(points),
        "telemetry_status": status,
        "excluded_reasons": points.loc[
            points["Plot Status"].eq("Excluded"), "Exclusion Reason"
        ].value_counts().to_dict(),
        "tied_hours": int(points["Tied Categories"].ne("").sum()),
        "beyond_display_range": int(points["Beyond Display Range"].sum()),
    }
    if audit["model_rows"] != audit["model_classified_hours"] + audit["model_unclassified_hours"]:
        raise ValueError("Model count reconciliation failed")
    if (
        audit["telemetry_rows"] != sum(status.values())
        or int(counts["Telemetry Hit Hours"].sum()) != status["Hit"]
        or int(counts["Telemetry Outside Hours"].sum()) != status["Outside threshold"]
        or sum(audit["excluded_reasons"].values()) != status["Excluded"]
    ):
        raise ValueError("Telemetry count reconciliation failed")
    return audit


def load_resource(name: str, pairs: list[tuple]) -> ResourceResult:
    """Read one resource's workbooks once and validate before deriving counts."""
    classified_parts, matched_parts, inputs = [], [], []
    for _, month, classified_path, matched_path in pairs:
        classified = validate_frame(pd.read_excel(
            classified_path, usecols=lambda col: col in KEYS + ["Month", "P_ESS_state"],
        ), name, month, "classified")
        matched = validate_frame(pd.read_excel(matched_path), name, month, "matched")
        classified_parts.append(classified)
        matched_parts.append(matched)
        inputs.append({
            "month": month, "model_rows": len(classified), "telemetry_rows": len(matched),
            "classified_file": classified_path.name, "matched_file": matched_path.name,
            "classified_sha256": hashlib.sha256(classified_path.read_bytes()).hexdigest(),
            "matched_sha256": hashlib.sha256(matched_path.read_bytes()).hexdigest(),
        })
    classified = pd.concat(classified_parts, ignore_index=True)
    if classified.duplicated(KEYS).any():
        raise ValueError(f"{name}: duplicate classified resource-hour across workbooks")
    points = classify_points(pd.concat(matched_parts, ignore_index=True))
    monthly, monthly_audit = [], []
    for _, month, *_ in pairs:
        model_month = classified.loc[classified["Month"].eq(month)]
        point_month = points.loc[points["Month"].eq(month)]
        counts = category_counts(model_month, point_month)
        counts.insert(0, "Month", month)
        counts.insert(0, "Resource Name", name)
        monthly.append(counts)
        monthly_audit.append({"month": month, **summarize(model_month, point_month)})
    return ResourceResult(
        name, category_counts(classified, points), pd.concat(monthly, ignore_index=True),
        points, {"resource": name, "inputs": inputs, "monthly": monthly_audit,
                 **summarize(classified, points)},
    )


def pool_results(resources: list[ResourceResult]) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Sum category counts and pool observations without collapsing shared hours."""
    counts = pd.concat([r.counts for r in resources]).groupby("Category")[COUNT_COLUMNS].sum()
    counts = counts.reindex(STATES).reset_index()
    points = display_positions(pd.concat([r.points for r in resources], ignore_index=True))
    audit = {
        key: sum(r.audit[key] for r in resources)
        for key in ("model_rows", "model_classified_hours", "model_unclassified_hours",
                    "telemetry_rows", "tied_hours", "beyond_display_range")
    }
    audit["telemetry_status"] = {
        s: sum(r.audit["telemetry_status"][s] for r in resources) for s in STATUSES
    }
    audit["excluded_reasons"] = points.loc[
        points["Plot Status"].eq("Excluded"), "Exclusion Reason"
    ].value_counts().to_dict()
    for state in STATES:
        for col in COUNT_COLUMNS:
            expected = sum(int(r.counts.set_index("Category").at[state, col]) for r in resources)
            if int(counts.set_index("Category").at[state, col]) != expected:
                raise ValueError(f"Aggregate reconciliation failed: {state}/{col}")
    if len(points) != audit["telemetry_rows"] or sum(audit["telemetry_status"].values()) != len(points):
        raise ValueError("Aggregate telemetry row reconciliation failed")
    for status in STATUSES:
        if int(points["Plot Status"].eq(status).sum()) != audit["telemetry_status"][status]:
            raise ValueError(f"Aggregate status reconciliation failed: {status}")
    if (
        int(counts["Model Hours"].sum()) != audit["model_classified_hours"]
        or audit["model_rows"] != audit["model_classified_hours"] + audit["model_unclassified_hours"]
        or int(counts["Telemetry Hit Hours"].sum()) != audit["telemetry_status"]["Hit"]
        or int(counts["Telemetry Outside Hours"].sum()) != audit["telemetry_status"]["Outside threshold"]
    ):
        raise ValueError("Aggregate category reconciliation failed")
    return counts, points, audit


def count_ticks(tables: list[pd.DataFrame]) -> np.ndarray:
    """Reserve label space and use about six ticks on a common count scale."""
    maximum = max(
        max(float(t["Model Hours"].max()),
            float(t["Telemetry Hit Hours"].max() + t["Telemetry Outside Hours"].max()))
        for t in tables
    )
    # The sum of component maxima bounds the independently interpolated profiles.
    return MaxNLocator(nbins=6, integer=True, steps=[1, 2, 2.5, 5, 10]).tick_values(
        0, max(maximum * 1.30, 5)
    )


def telemetry_profile(counts: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Keep category totals while closing the profile at the outer bar edges."""
    ordered = counts.set_index("Category").loc[list(STATES)]
    half_height = ROLLING_BAR_HEIGHT / 2
    support = np.concatenate(([-half_height], np.arange(len(STATES)),
                              [len(STATES) - 1 + half_height]))
    # Include category centers exactly so the drawn profile retains their totals.
    smooth_y = np.unique(np.concatenate((np.linspace(support[0], support[-1], 700), support)))
    components = [
        np.maximum(PchipInterpolator(
            support, np.concatenate(([0.0], ordered[column].to_numpy(dtype=float), [0.0]))
        )(smooth_y), 0.0)
        for column in ("Telemetry Hit Hours", "Telemetry Outside Hours")
    ]
    total_width = components[0] + components[1]
    total_width[[0, -1]] = 0.0  # Remove floating-point residue at the closing tips.
    return smooth_y, total_width


def draw_count_panel(
    ax, counts: pd.DataFrame, ticks: np.ndarray, *, label_size: float = 10,
) -> None:
    """Overlay category bars on the preserved telemetry total silhouette."""
    # Remove only the old left data artists; retain axes and junction tick offsets.
    for artist in [*ax.collections, *ax.images, *ax.lines, *ax.texts, *ax.patches]:
        artist.remove()
    ordered = counts.set_index("Category").loc[list(STATES)]
    category_y = np.arange(len(STATES))
    smooth_y, total_width = telemetry_profile(counts)
    silhouette = ax.fill_betweenx(
        smooth_y, 0, total_width, facecolor="none", edgecolor="none", zorder=1,
    )
    gradient_y = np.linspace(smooth_y[0], smooth_y[-1], 1000)
    gradient = example.category_gradient(gradient_y)[:, None, :]
    fill = ax.imshow(
        gradient, extent=(0, float(ticks[-1]), smooth_y[0], smooth_y[-1]),
        origin="lower", aspect="auto", interpolation="bilinear",
        alpha=PROFILE_ALPHA, zorder=1,
    )
    fill.set_clip_path(silhouette.get_paths()[0], ax.transData)
    ax.plot(total_width, smooth_y, color="#555555", linestyle="--",
            linewidth=1.2, zorder=2)
    colors = [example.COLORS[state] for state in STATES]
    styles = (("Model Hours", ROLLING_BAR_HEIGHT, 0.25, "-"),
              ("Telemetry Hit Hours", 0.22, 0.65, "--"))
    for column, height, alpha, _ in styles:
        ax.barh(category_y, ordered[column], height=height,
                color=[to_rgba(color, alpha) for color in colors],
                edgecolor="none", zorder=3)
    # Draw both boundaries after both fills, so even shorter bars stay visible.
    for column, height, _, linestyle in styles:
        ax.barh(category_y, ordered[column], height=height, facecolor="none",
                edgecolor=colors, linewidth=1.1, linestyle=linestyle, zorder=4)
    ax.set_xlim(float(ticks[-1]), 0)
    ax.set_xticks(ticks, [f"{tick:,.0f}" for tick in ticks])
    ax.set_xlabel("Resource-hours", labelpad=10)

    label_offset = 6
    axes_height_points = ax.figure.get_figheight() * ax.get_position().height * 72
    label_band = (label_offset + 1.5 * label_size) * abs(np.diff(ax.get_ylim())[0]) / axes_height_points
    for y, state in enumerate(STATES):
        model = int(ordered.at[state, "Model Hours"])
        hits = int(ordered.at[state, "Telemetry Hit Hours"])
        nearby = np.abs(smooth_y - y) <= label_band
        label_x = max(model, hits, float(total_width[nearby].max()))
        for value, offset, alignment, color in (
            (model, label_offset, "bottom", example.COLORS[state]),
            (hits, -label_offset, "top", "#000000"),
        ):
            ax.annotate(
                f"{value:,}", (label_x, y), xytext=(-9, offset),
                textcoords="offset points", va=alignment, ha="right",
                fontsize=label_size, color=color, zorder=5,
            )


def draw_figure(counts, points, audit, title: str, ticks: np.ndarray, *, pooled=False):
    """Replace left profiles with bars while preserving the right renderer."""
    plt.rcParams.update({
        "font.family": "Times New Roman", "pdf.fonttype": 42, "svg.fonttype": "none",
    })
    fig = example.draw_example(counts, points, audit)
    count_ax, scatter_ax = fig.axes
    # The pooled figure is reduced to manuscript text width; reserve room for
    # larger type without shrinking or subsampling its resource-hour marks.
    if pooled:
        fig.set_size_inches(14.0, 9.2)
        fig.subplots_adjust(bottom=0.14)
    # These are the existing batch-specific adjustments to the right panel.
    for collection in scatter_ax.collections:
        collection.set_rasterized(True)
    for annotation in scatter_ax.texts:
        if annotation.get_text() in STATES:
            annotation.set_position((5, 14 if pooled else 12))
            if pooled:
                annotation.set_fontsize(20)
        elif pooled:
            annotation.set_fontsize(15)
    fig.suptitle(
        title.replace(" | ", "   |   ") if pooled else title,
        x=0.5, y=0.985 if pooled else 0.993,
        fontsize=22 if pooled else 13, fontweight="bold" if pooled else "normal",
    )
    fig.subplots_adjust(top=0.79)
    draw_count_panel(count_ax, counts, ticks, label_size=17 if pooled else 10)
    if pooled:
        # Fewer tick labels leave room for the enlarged type at the axis junction.
        display_ticks = np.unique(np.concatenate((ticks[::2], ticks[-1:])))
        count_ax.set_xticks(display_ticks, [f"{tick:,.0f}" for tick in display_ticks])
        scatter_ax.set_xticks([-0.5, -0.25, 0, 0.25, 0.5])
        for ax in (count_ax, scatter_ax):
            ax.tick_params(axis="x", labelsize=16)
            ax.xaxis.label.set_fontsize(20)

    legend = fig.legends[0]
    right_handles = list(legend.legend_handles)[3:]
    right_labels = [text.get_text() for text in legend.get_texts()][3:]
    legend.remove()
    color = example.COLORS["DD"]
    handles = [
        Patch(facecolor=to_rgba(color, 0.25), edgecolor=color, linewidth=1.1,
              linestyle="-", label="Rolling count"),
        Patch(facecolor=to_rgba(color, 0.65), edgecolor=color, linewidth=1.1,
              linestyle="--", label="Telemetry hit count"),
        Line2D([], [], color="#555555", linewidth=1.2, linestyle="--",
               label="Telemetry total (gradient)"),
    ]
    fig.legend(
        handles + right_handles,
        [handle.get_label() for handle in handles] + right_labels,
        loc="upper center", bbox_to_anchor=(0.5, 0.925 if pooled else 0.95),
        frameon=False, ncol=3, borderaxespad=0, fontsize=17 if pooled else 11,
        columnspacing=1.5 if pooled else 2.0, labelspacing=0.9,
    )
    fig.text(
        0.05, 0.025,
        "Bars: light = Rolling; dark = telemetry hits. Gradient profile = telemetry total. "
        + ("\n" if pooled else "") + "All eligible resource-hours; no subsampling.",
        fontsize=14 if pooled else 9, color="#444444",
    )
    return fig


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--data-output-dir", type=Path, help="Statistics/audit directory; default: configured results root / figure_data/Classification_All")
    args = parser.parse_args(argv)
    config, results_root, paths = input_contract(args.experiment.resolve())
    resources, failures = [], []
    for name in config["members"]:
        try:
            result = load_resource(name, [p for p in paths if p[0] == name])
            resources.append(result)
            print(f"Loaded {name}: {result.audit['telemetry_rows']} telemetry rows", flush=True)
        except (ValueError, TypeError, OSError, KeyError) as exc:
            failures.append(f"{name}: {exc}")
    if failures:
        raise ValueError("No figures exported; invalid inputs:\n" + "\n".join(failures))
    counts, points, aggregate_audit = pool_results(resources)
    individual_ticks = count_ticks([r.counts for r in resources])
    aggregate_ticks = count_ticks([counts])
    months = config["months"]
    period = f"{months[0].replace('-', '')}_{months[-1].replace('-', '')}"
    date_label = f"{months[0]} to {months[-1]}"
    if pd.period_range(months[0], months[-1], freq="M").astype(str).tolist() != months:
        date_label = ", ".join(months)
    output = args.output_dir.resolve()
    data_output = (args.data_output_dir or results_root / 'figure_data/Classification_All').resolve()
    if data_output == output:
        raise ValueError('Figure and statistical output directories must differ')
    output.mkdir(parents=True, exist_ok=True)
    data_output.mkdir(parents=True, exist_ok=True)
    jobs = [
        (r.name, r.counts, r.points, r.audit, individual_ticks,
         f"{r.name} | {date_label}") for r in resources
    ] + [("ALL_ESR", counts, points, aggregate_audit, aggregate_ticks,
          f"All {len(resources)} ESRs | {date_label} | Pooled resource-hours")]
    outputs = []
    for name, table, observations, audit, ticks, title in jobs:
        fig = draw_figure(table, observations, audit, title, ticks, pooled=name == "ALL_ESR")
        try:
            for suffix in (".png", ".pdf"):
                path = output / f"Classification_SharedCategory_{name}_{period}{suffix}"
                fig.savefig(path, dpi=240, facecolor="white")
                outputs.append(path.name)
        finally:
            plt.close(fig)
        print(f"Rendered {name}", flush=True)
    pd.concat([r.monthly_counts for r in resources], ignore_index=True).to_csv(
        data_output / f"classification_counts_by_resource_month_{period}.csv", index=False,
    )
    pd.concat([r.counts.assign(**{"Resource Name": r.name}) for r in resources]).to_csv(
        data_output / f"classification_counts_by_resource_{period}.csv", index=False,
    )
    counts.to_csv(data_output / f"classification_counts_aggregate_{period}.csv", index=False)
    # Individual coordinates remain auditable after aggregate jitter is regenerated.
    individual_y = pd.concat([r.points["Display Y"] for r in resources], ignore_index=True)
    original_points = pd.concat([r.points for r in resources], ignore_index=True)
    original_points["Individual Display Y"] = individual_y
    exported_points = display_positions(original_points)
    exported_points.to_csv(data_output / f"classification_telemetry_hours_{period}.csv", index=False)
    report = {
        "resources": list(config["members"]), "months": months,
        "resource_count": len(resources), "resource_month_pairs": len(paths),
        "aggregation": "Pool resource-hours; no capacity or equal-resource weighting",
        "threshold": example.THRESHOLD, "threshold_rule": "abs(gap) < threshold",
        "tie_rule": "Tolerance 1e-9; NU, then power-sign family, then canonical category order",
        "categories": STATES, "deviation_axis_limits": [-example.AXIS_LIMIT, example.AXIS_LIMIT],
        "distance_basis": "Existing clipped-power signed normalized gaps; nearest available category",
        "sampling": "All eligible observations; none subsampled",
        "profiles": "Telemetry total: sum of nonnegative hit and outside-threshold PCHIP count profiles; not MW density or KDE",
        "profile_display": "Category gradient; endpoints aligned to MD/MC Rolling bar edges; unscaled count widths",
        "bars": "Overlapping leftward category counts: Rolling (Model Hours) and telemetry hits (Telemetry Hit Hours)",
        "individual_count_ticks": individual_ticks.tolist(),
        "aggregate_count_ticks": aggregate_ticks.tolist(),
        "aggregate": aggregate_audit, "individual": [r.audit for r in resources],
        "outputs": outputs,
        "directory_path_rule": "Relative to the release root for in-release paths; absolute for external output overrides",
        "figure_directory": output.relative_to(ROOT).as_posix() if output.is_relative_to(ROOT) else output.as_posix(),
        "data_directory": data_output.relative_to(ROOT).as_posix() if data_output.is_relative_to(ROOT) else data_output.as_posix(),
        "count_reconciliation": "passed",
    }
    (data_output / f"classification_audit_{period}.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8",
    )
    print(json.dumps(aggregate_audit, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
