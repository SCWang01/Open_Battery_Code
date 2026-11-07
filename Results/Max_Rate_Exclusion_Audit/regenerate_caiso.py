"""Regenerate CAISO width artifacts and verify unchanged non-width results."""

from __future__ import annotations

import ast
import csv
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from openpyxl import load_workbook
from Program import process_bidding_dsfunction as pipeline
from Figure_Plot.figure_plot_4_a_b import plot_state_distribution_pies as plot
from Figure_Plot.figure_plot_4_c import export_hourly_state_counts as hourly

AUDIT_DIR = Path(__file__).resolve().parent
BASELINE = AUDIT_DIR / "caiso_before.json"


def digest(value):
    payload = json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def workbook_rows(path):
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        rows = workbook.active.iter_rows(values_only=True)
        headers = next(rows)
        return headers, list(rows)
    finally:
        workbook.close()


def snapshot():
    books = {}
    inputs = {}
    for year, month, path in pipeline.discover_inputs(pipeline.DEFAULT_BIDDING_DIR):
        inputs[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        output = pipeline.output_path_for(path, pipeline.DEFAULT_BIDDING_DIR)
        headers, rows = workbook_rows(output)
        width_index = headers.index("dsfunction_state_width")
        state_index = headers.index("P_ESS_state")
        widths = [ast.literal_eval(row[width_index]) if row[width_index] else None for row in rows]
        books[output.name] = {
            "year": year,
            "month": month,
            "rows": len(rows),
            "non_width_sha256": digest([
                [value for i, value in enumerate(row) if i != width_index]
                for row in rows
            ]),
            "states": dict(Counter(str(row[state_index]) for row in rows)),
            "widths": widths,
        }
    headers, rows = workbook_rows(pipeline.DEFAULT_SUMMARY)
    summary_non_width = digest([
        [value for i, value in enumerate(row) if not headers[i].startswith("width_")]
        for row in rows
    ])
    c_period = hourly.PeriodSpec("All", tuple(pipeline.EXPECTED_MONTHS), "all")
    tally = hourly.aggregate_period(c_period)
    c_tally = {
        "counts": {hour: dict(counts) for hour, counts in tally.counts.items()},
        "valid": dict(tally.valid),
        "excluded_zero_limit": dict(tally.excluded_zero_limit),
        "excluded_nc": dict(tally.excluded_nc),
        "raw": dict(tally.raw),
    }
    figures = {}
    for path in plot.OUTPUT_DIR.glob("*_source_data.csv"):
        with path.open(encoding="utf-8-sig", newline="") as handle:
            figures[path.name] = list(csv.DictReader(handle))
    return {
        "books": books,
        "raw_input_sha256": inputs,
        "summary_non_width_sha256": summary_non_width,
        "panel_c_tally_sha256": digest(c_tally),
        "figures": figures,
    }


def periods():
    for year in plot.SELECTION_YEARS:
        yield str(year), [(year, month) for month in range(1, 13)]
    yield "2023-2025", list(pipeline.EXPECTED_MONTHS)
    for season in plot.SEASON_MONTHS:
        yield season, plot.select_months(plot.season_selection_matrix(season))


def main():
    # Preserve the original comparison baseline if this audit is run again.
    if not BASELINE.exists():
        BASELINE.write_text(json.dumps(snapshot(), indent=2), encoding="utf-8")
    before = json.loads(BASELINE.read_text(encoding="utf-8"))
    pipeline.process_all(
        pipeline.DEFAULT_BIDDING_DIR,
        pipeline.DEFAULT_BIDDING_DIR,
        pipeline.DEFAULT_SUMMARY,
    )
    after = snapshot()
    assert before["raw_input_sha256"] == after["raw_input_sha256"], "Raw optimization inputs changed"
    assert before["summary_non_width_sha256"] == after["summary_non_width_sha256"], "Non-width summary changed"
    assert before["panel_c_tally_sha256"] == after["panel_c_tally_sha256"], "Panel c hourly tally changed"

    changes = []
    for name, previous in before["books"].items():
        current = after["books"][name]
        assert previous["non_width_sha256"] == current["non_width_sha256"], name
        assert previous["states"] == current["states"], name
        changed_rows = []
        for excel_row, (old, new) in enumerate(zip(previous["widths"], current["widths"]), 2):
            if old is None or new is None:
                assert old == new, (name, excel_row)
            elif any(not math.isclose(a, b, rel_tol=0, abs_tol=1e-8) for a, b in zip(old, new)):
                changed_rows.append(excel_row)
        changes.append({"workbook": name, "changed_width_rows": changed_rows})

    figures = []
    written_files = [str(pipeline.DEFAULT_SUMMARY.relative_to(ROOT))]
    written_files.extend(str((pipeline.DEFAULT_BIDDING_DIR / name).relative_to(ROOT)) for name in before["books"])
    for label, months in periods():
        basename = f"state_distribution_pies_{plot.period_basename_suffix(months)}"
        previous = before["figures"][f"{basename}_source_data.csv"]
        states, widths, records = plot.read_source_data(months)
        rows = plot.source_rows(states, widths, records)
        for old, new in zip(previous, rows):
            assert old["state"] == new["state"]
            for field in ("p_ess_count", "p_ess_percent", "legend_label", "color_hex"):
                assert str(old[field]) == str(new[field]), (label, field, old, new)
        csv_path = plot.write_source_csv(rows, basename)
        figure = plot.build_figure(states, widths, records)
        paths = plot.save_figure(figure, basename, plot.period_description(months))
        plot.plt.close(figure)
        written_files.extend(str(path.relative_to(ROOT)) for path in [csv_path, *paths])
        figures.append({
            "period": label,
            "records": records,
            "panel_b_unchanged": True,
            "old_percent": {r["state"]: float(r["dsfunction_left_normalized_percent"]) for r in previous if r["included_in_left_pie"] == "true"},
            "new_percent": {state: 100 * value / sum(widths.values()) for state, value in widths.items()},
            "files": [str(path.relative_to(ROOT)) for path in [csv_path, *paths]],
        })
        print(f"Regenerated {label}: {records} records; panel b unchanged", flush=True)

    result = {
        "backend": "python",
        "workbook_count": len(changes),
        "hourly_rows": sum(book["rows"] for book in before["books"].values()),
        "changed_width_rows": sum(len(book["changed_width_rows"]) for book in changes),
        "raw_optimization_inputs_unchanged": True,
        "all_non_width_workbook_cells_unchanged": True,
        "non_width_summary_unchanged": True,
        "panel_c_hourly_tally_unchanged": True,
        "workbook_changes": changes,
        "figures": figures,
        "written_files": written_files,
    }
    (AUDIT_DIR / "caiso_audit.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"CAISO: {len(changes)} workbooks; {result['changed_width_rows']} width rows changed; {len(figures)} figures regenerated", flush=True)


if __name__ == "__main__":
    main()
