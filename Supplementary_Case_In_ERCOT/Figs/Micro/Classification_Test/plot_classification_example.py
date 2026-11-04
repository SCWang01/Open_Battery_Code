"""Compare left half-count profiles and right deviations at a central Y-axis.

Use BIG_STAR_ESR1 monthly hourly results for January-June 2026. Classification
and exclusions retain the trial's nearest-category and strict abs(gap)<0.03 rules.
Left half-outlines attach to the central category axis, with count increasing
leftward from zero. They interpolate seven counts with PCHIP on a common scale;
their smooth connections are decorative, not continuous power distributions.
Telemetry includes colored hits and gray non-hits; their separately interpolated
nonnegative counts sum to the total outline. Category-colored numbers show
Rolling classified counts; black numbers show telemetry hit counts. Both labels
sit outside the combined profiles; non-hit counts remain visible as gray width.
The count and deviation regions use a 1:2 width ratio. The right region runs
from -50% at the junction to +50% at its edge;
its deviation zero is separate from the central category axis. Points retain
exact signed deviations and use deterministic vertical jitter.
Category colors blend vertically in the profiles and the +/-3% threshold band.
Export PNG and PDF files for the selected resource.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.colors import to_rgb
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import PercentFormatter
from matplotlib.transforms import ScaledTranslation
import numpy as np
import pandas as pd
from scipy.interpolate import PchipInterpolator


ROOT = Path(__file__).resolve().parents[3]
OUTPUT = Path(__file__).resolve().parent
RESOURCE = "BIG_STAR_ESR1"
MONTHS = [f"2026{month:02d}" for month in range(1, 7)]
STATES = ("MD", "DD", "DC", "NU", "CD", "CC", "MC")
THRESHOLD = 0.03
TIE_TOLERANCE = 1e-9
AXIS_LIMIT = 0.5
DISCHARGE_STATES = frozenset(("MD", "DD", "DC"))
CHARGE_STATES = frozenset(("MC", "CC", "CD"))
COLORS = {
    "MC": "#C90000", "CC": "#FF6B6B", "CD": "#F6B4B4",
    "NU": "#A6A6A6", "DC": "#9CC5E5", "DD": "#4F95D5", "MD": "#1D4B73",
}
VERTICAL_JITTER = 0.22
NON_HIT_COLOR = "#8A8A8A"


def nearest_category(gaps, power):
    """Resolve one closest available category without changing its signed gap."""
    minimum = min(abs(value) for value in gaps.values())
    candidates = [state for state in STATES if state in gaps and abs(abs(gaps[state]) - minimum) <= TIE_TOLERANCE]
    if "NU" in candidates:
        return "NU", candidates
    preferred = DISCHARGE_STATES if power > 0 else CHARGE_STATES if power < 0 else frozenset()
    return next((state for state in candidates if state in preferred), candidates[0]), candidates


def load_example():
    classified_parts, matched_parts = [], []
    for month in MONTHS:
        classified_path = ROOT / "Results/Micro_Results/Classified" / f"classified_{RESOURCE}_1hour_{month}.xlsx"
        matched_path = ROOT / "Results/Micro_Results/realized_match" / f"Realized_Match_{RESOURCE}_1hour_{month}.xlsx"
        classified_parts.append(pd.read_excel(
            classified_path,
            usecols=["Resource Name", "Hour Start UTC", "P_ESS_state"],
        ))
        matched_parts.append(pd.read_excel(matched_path))
    classified = pd.concat(classified_parts, ignore_index=True)
    matched = pd.concat(matched_parts, ignore_index=True)
    for name, frame in (("classified", classified), ("matched", matched)):
        if not frame["Resource Name"].eq(RESOURCE).all():
            raise ValueError(f"{name}: inconsistent resource")
        if name == "classified" and (frame["Hour Start UTC"].isna().any() or frame["Hour Start UTC"].duplicated().any()):
            raise ValueError(f"{name}: missing or duplicated UTC hour")
    matched = matched.sort_values("Hour Start UTC").reset_index(drop=True)
    matched["Nearest Category"] = ""
    matched["Nearest Gap"] = np.nan
    matched["Plot Status"] = "Excluded"
    matched["Exclusion Reason"] = ""
    matched["Tied Categories"] = ""
    duplicate = matched["Hour Start UTC"].duplicated(keep=False)
    for index, row in matched.iterrows():
        note = str(row.get("Notes", ""))
        reason = ""
        if pd.isna(row["Hour Start UTC"]) or duplicate.iloc[index]:
            reason = "Missing or duplicate UTC hour"
        elif "ZERO_POWER_RANGE" in note:
            reason = "Zero effective power range"
        elif any(tag in note for tag in ("MISSING_ROLLING", "MISSING_TELEMETRY", "DUPLICATE_ROLLING_TIME", "DUPLICATE_TELEMETRY_TIME")):
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
        nearest, candidates = nearest_category(gaps, power)
        matched.at[index, "Nearest Category"] = nearest
        matched.at[index, "Nearest Gap"] = gaps[nearest]
        matched.at[index, "Plot Status"] = "Hit" if abs(gaps[nearest]) < THRESHOLD else "Outside threshold"
        if len(candidates) > 1:
            matched.at[index, "Tied Categories"] = "|".join(candidates)
    matched["Display X"] = matched["Nearest Gap"]
    matched["Display Y"] = np.nan
    for index, state in enumerate(STATES):
        selected_rows = matched.index[matched["Nearest Category"].eq(state)]
        # Spread only the categorical coordinate; signed deviations remain exact.
        offset = ((np.arange(len(selected_rows)) * 0.6180339887498949) % 1 - 0.5) * VERTICAL_JITTER
        matched.loc[selected_rows, "Display Y"] = index + offset
    matched["Beyond Display Range"] = matched["Nearest Gap"].abs().gt(AXIS_LIMIT)
    counts = pd.DataFrame({"Category": STATES})
    counts["Model Hours"] = [int(classified["P_ESS_state"].eq(state).sum()) for state in STATES]
    counts["Telemetry Hit Hours"] = [
        int((matched["Nearest Category"].eq(state) & matched["Plot Status"].eq("Hit")).sum())
        for state in STATES
    ]
    counts["Telemetry Outside Hours"] = [
        int((matched["Nearest Category"].eq(state) & matched["Plot Status"].eq("Outside threshold")).sum())
        for state in STATES
    ]
    audit = {
        "resource": RESOURCE, "months": MONTHS, "threshold": THRESHOLD,
        "model_rows": len(classified), "model_classified_hours": int(counts["Model Hours"].sum()),
        "model_unclassified_hours": int((~classified["P_ESS_state"].isin(STATES)).sum()),
        "telemetry_rows": len(matched), "telemetry_status": matched["Plot Status"].value_counts().to_dict(),
        "excluded_reasons": matched.loc[matched["Plot Status"].eq("Excluded"), "Exclusion Reason"].value_counts().to_dict(),
        "tied_hours": int(matched["Tied Categories"].ne("").sum()),
        "tie_rule": "Within absolute tolerance 1e-9: NU, then matched-power charge/discharge family, then MD/DD/DC/NU/CD/CC/MC",
        "distance_basis": "Existing clipped-power signed normalized gaps; nearest available category",
        "horizontal_position": "Exact signed nearest-category deviation",
        "vertical_position": "Nearest category plus deterministic display jitter",
        "deviation_axis_limits": [-AXIS_LIMIT, AXIS_LIMIT],
        "beyond_display_range": int(matched["Beyond Display Range"].sum()),
        "overflow_display": "Outward triangle at axis boundary; original signed gap retained in hour data",
        "profiles": "Common count scale; telemetry total sums separate nonnegative hit and non-hit PCHIP curves",
        "unclassified_points": "Outside-threshold hours are grouped by nearest category, not classified as hits",
        "sampling": "All eligible hours shown; no subsampling",
    }
    return counts, matched, audit


def category_gradient(y_values):
    """Interpolate RGB colors between category rows without changing the data."""
    base = np.array([to_rgb(COLORS[state]) for state in STATES])
    return np.column_stack([
        np.interp(y_values, np.arange(len(STATES)), base[:, channel])
        for channel in range(3)
    ])


def draw_example(counts, points, audit, palette=None, rolling_points=None):
    """Render central categories; retain legacy arguments for existing callers.

    The category palette is fixed by COLORS for this trial. Legacy source-color
    palettes and power-point inputs do not alter this count/deviation display.
    """
    font_manager.findfont("Times New Roman", fallback_to_default=False)
    plt.rcParams.update({
        "font.family": "Times New Roman", "font.size": 12,
        "axes.spines.top": False, "axes.spines.right": False,
        "pdf.fonttype": 42, "axes.unicode_minus": True,
    })
    fig, (count_ax, scatter_ax) = plt.subplots(
        1, 2, figsize=(14.0, 8.0), sharey=True,
        gridspec_kw={"width_ratios": [1, 2]},
    )
    fig.subplots_adjust(left=0.05, right=0.95, bottom=0.10, top=0.84, wspace=0)
    y_min, y_max = -0.65, len(STATES) - 0.35
    count_ax.set_ylim(y_max, y_min)
    count_ax.set_yticks(np.arange(len(STATES)))
    count_ax.spines["left"].set_visible(False)
    # Only the junction spine is visible: count zero on its left, -50% on right.
    scatter_ax.spines["left"].set_visible(True)
    scatter_ax.spines["left"].set_color("#444444")
    scatter_ax.spines["left"].set_linewidth(1.0)
    scatter_ax.spines["left"].set_zorder(6)
    for axis in (count_ax, scatter_ax):
        axis.tick_params(axis="y", left=False, right=False,
                         labelleft=False, labelright=False)
        axis.set_facecolor("white")
        axis.yaxis.grid(True, color="#E8EBEF", linewidth=0.55)
        axis.set_axisbelow(True)
    for y, state in enumerate(STATES):
        scatter_ax.annotate(
            state, (0, y), xycoords=scatter_ax.get_yaxis_transform(),
            xytext=(5, 0), textcoords="offset points", ha="left", va="center",
            fontsize=13, color=COLORS[state], zorder=6,
        )

    gradient_y = np.linspace(y_min, y_max, 1000)
    gradient = category_gradient(gradient_y)[:, None, :]
    ordered = counts.set_index("Category").loc[list(STATES)]
    category_y = np.arange(len(STATES), dtype=float)
    support = np.concatenate(([-0.45], category_y, [len(STATES) - 0.55]))
    smooth_y = np.linspace(support[0], support[-1], 700)
    curves = {}
    for column in ("Model Hours", "Telemetry Hit Hours", "Telemetry Outside Hours"):
        values = ordered[column].to_numpy(dtype=float)
        curve = PchipInterpolator(support, np.concatenate(([0.0], values, [0.0])))(smooth_y)
        curves[column] = np.maximum(curve, 0.0)
    rolling_width = curves["Model Hours"]
    hit_width = curves["Telemetry Hit Hours"]
    outside_width = curves["Telemetry Outside Hours"]
    # Sum the interpolated components, rather than subtracting independent total
    # and hit interpolants, so the gray region can never have negative width.
    total_width = hit_width + outside_width
    maximum = max(float(rolling_width.max()), float(total_width.max()), 1.0)
    tick_maximum = float(np.ceil(maximum / 1000.0) * 1000.0)
    count_limit = tick_maximum + 1000.0
    for half_width, alpha in ((rolling_width, 0.28), (hit_width, 0.52)):
        # Count zero fixes the straight edge to the junction. Reversing the
        # count axis below makes positive widths extend only to the left.
        silhouette = count_ax.fill_betweenx(
            smooth_y, 0, half_width, facecolor="none", edgecolor="none",
        )
        fill = count_ax.imshow(
            gradient, extent=(0, count_limit, y_min, y_max), origin="lower",
            aspect="auto", interpolation="bilinear", alpha=alpha, zorder=2,
        )
        fill.set_clip_path(silhouette.get_paths()[0], count_ax.transData)
    count_ax.fill_betweenx(
        smooth_y, hit_width, total_width,
        facecolor=NON_HIT_COLOR, edgecolor="none", alpha=0.65, zorder=3,
    )
    # Keep both boundaries visible above every fill, including gray non-hits.
    for half_width, linestyle in ((rolling_width, "-"), (total_width, "--")):
        count_ax.plot(half_width, smooth_y, color="#3F3F3F",
                      linewidth=1.2, linestyle=linestyle, zorder=4)
    label_size = 10
    label_offset = 6
    axes_height_points = fig.get_figheight() * count_ax.get_position().height * 72
    # Reserve the full height of both labels, including their vertical offsets.
    # Use the outermost profile throughout that band so a sloping curve cannot
    # cross either number even when its width changes sharply between rows.
    label_band = (label_offset + 1.5 * label_size) * (y_max - y_min) / axes_height_points
    outer_width = np.maximum(rolling_width, total_width)
    for y, state in zip(category_y, STATES):
        model = int(ordered.at[state, "Model Hours"])
        hits = int(ordered.at[state, "Telemetry Hit Hours"])
        nearby = np.abs(smooth_y - y) <= label_band
        label_x = max(
            float(outer_width[nearby].max()), model,
            hits + int(ordered.at[state, "Telemetry Outside Hours"]),
        )
        count_ax.annotate(
            f"{model:,}", (label_x, y), xytext=(-9, label_offset),
            textcoords="offset points", va="bottom", ha="right",
            fontsize=label_size, color=COLORS[state], zorder=5,
        )
        count_ax.annotate(
            f"{hits:,}", (label_x, y), xytext=(-9, -label_offset),
            textcoords="offset points", va="top", ha="right",
            fontsize=label_size, color="#000000", zorder=5,
        )
    count_ax.set_xlim(count_limit, 0)
    count_ticks = np.arange(0, count_limit + 1000.0, 1000.0)
    count_ax.set_xticks(count_ticks, [f"{tick:,.0f}" for tick in count_ticks])
    count_ax.tick_params(axis="x", labelsize=10)
    count_ax.set_xlabel("Count", labelpad=10)

    scatter_ax.imshow(
        gradient, extent=(-THRESHOLD, THRESHOLD, y_min, y_max), origin="lower",
        aspect="auto", interpolation="bilinear", alpha=0.13, zorder=0,
    )
    scatter_ax.axvline(0, color="#666666", linewidth=0.7, zorder=1)
    for boundary in (-THRESHOLD, THRESHOLD):
        scatter_ax.axvline(boundary, color="#666666", linestyle="--",
                           linewidth=0.8, zorder=2)
    # Boundary labels sit in the top margin inside the axes, away from MD points.
    for boundary, alignment in ((-THRESHOLD, "right"), (THRESHOLD, "left")):
        scatter_ax.annotate(
            f"{boundary:+.0%}", (boundary, y_min),
            xytext=(-3 if boundary < 0 else 3, -7), textcoords="offset points",
            ha=alignment, va="top", fontsize=10, color="#555555",
        )

    valid = points.loc[points["Plot Status"].isin(("Hit", "Outside threshold"))]
    for state in STATES:
        group = valid.loc[valid["Nearest Category"].eq(state)]
        category_color = np.asarray(to_rgb(COLORS[state]))
        visible = group.loc[group["Nearest Gap"].abs().le(AXIS_LIMIT)]
        for status, marker, color, size, alpha in (
            ("Hit", "o", category_color, 7, 0.55),
            ("Outside threshold", "x", NON_HIT_COLOR, 14, 0.80),
        ):
            selected = visible.loc[visible["Plot Status"].eq(status)]
            scatter_ax.scatter(
                selected["Nearest Gap"], selected["Display Y"],
                color=color, marker=marker, s=size, alpha=alpha,
                linewidths=0.6 if marker == "x" else 0,
                rasterized=True, zorder=3,
            )
        for direction, marker in ((-1, "<"), (1, ">")):
            overflow = group.loc[group["Nearest Gap"].mul(direction).gt(AXIS_LIMIT)]
            if len(overflow):
                scatter_ax.scatter(
                    np.full(len(overflow), direction * AXIS_LIMIT), overflow["Display Y"],
                    color=NON_HIT_COLOR, marker=marker, s=25, clip_on=False, zorder=4,
                )
                scatter_ax.annotate(
                    str(len(overflow)), (direction * AXIS_LIMIT, STATES.index(state)),
                    xytext=(-7 * direction, -14), textcoords="offset points",
                    ha="right" if direction > 0 else "left", va="top", fontsize=9,
                )

    scatter_ax.set_xlim(-AXIS_LIMIT, AXIS_LIMIT)
    scatter_ax.set_xticks([tick / 10 for tick in range(-5, 6)])
    scatter_ax.xaxis.set_major_formatter(PercentFormatter(xmax=1, decimals=0))
    scatter_ax.set_xlabel("Normalized power deviation", labelpad=10)
    # The two bottom scales meet, but their junction labels sit on either side.
    for label, alignment, offset in (
        (count_ax.get_xticklabels()[0], "right", -3),
        (scatter_ax.get_xticklabels()[0], "left", 3),
    ):
        label.set_horizontalalignment(alignment)
        label.set_transform(
            label.get_transform() + ScaledTranslation(offset / 72, 0, fig.dpi_scale_trans)
        )
    legend_handles = [
        Line2D([], [], color="#3F3F3F", linestyle="-", linewidth=1.2, label="Rolling"),
        Line2D([], [], color="#3F3F3F", linestyle="--", linewidth=1.2,
               label="Telemetry total"),
        Patch(facecolor=NON_HIT_COLOR, alpha=0.65, edgecolor="none",
              label="Telemetry non-hit count"),
        Line2D([], [], color="#555555", marker="o", linestyle="none", markersize=4,
               label="Hit: |d| < 3%"),
        Line2D([], [], color=NON_HIT_COLOR, marker="x", linestyle="none", markersize=5,
               markeredgewidth=0.6, label="Non-hit: |d| ≥ 3%"),
        Line2D([], [], color="#666666", linestyle="--", linewidth=0.8,
               label="±3% threshold"),
    ]
    overflow_count = int(valid["Nearest Gap"].abs().gt(AXIS_LIMIT).sum())
    if overflow_count:
        legend_handles.append(Line2D([], [], color=NON_HIT_COLOR, marker=">", linestyle="none",
                                     label=f"Outside ±50%: {overflow_count}"))
    fig.legend(handles=legend_handles, loc="upper center", bbox_to_anchor=(0.50, 0.975),
               frameon=False, ncol=3, borderaxespad=0, fontsize=11,
               columnspacing=2.0, labelspacing=0.9)
    return fig


def main():
    counts, points, audit = load_example()
    prefix = OUTPUT / f"Classification_SharedCategory_{RESOURCE}_202601_202606"
    fig = draw_example(counts, points, audit)
    try:
        for suffix in ("png", "pdf"):
            fig.savefig(f"{prefix}.{suffix}", dpi=240, facecolor="white")
    finally:
        plt.close(fig)
    print(f"Rolling classified: {audit['model_classified_hours']}; "
          f"telemetry status: {audit['telemetry_status']}")
    print(f"Figures: {prefix}.png and {prefix}.pdf")


if __name__ == "__main__":
    main()
