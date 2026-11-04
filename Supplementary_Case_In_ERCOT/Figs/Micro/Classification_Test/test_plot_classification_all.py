"""Focused semantic checks; run with python -B on this file."""

from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd

# Keep direct test execution independent of the working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import plot_classification_all as batch


def observations(specs, resource="TEST_ESR"):
    rows = []
    for index, spec in enumerate(specs):
        row = {
            "Resource Name": resource, "Month": "2026-01",
            "Hour Start UTC": pd.Timestamp("2026-01-01 06:00") + pd.Timedelta(hours=index),
            "Telemetered Net Output": 1.0, batch.NOTE_COLUMN: "",
            **{state: np.nan for state in batch.STATES}, **spec,
        }
        rows.append(row)
    return pd.DataFrame(rows)


def resource_result(name, specs):
    points = batch.classify_points(observations(specs, name))
    classified = pd.DataFrame({"P_ESS_state": ["MD"] * len(points)})
    return batch.ResourceResult(
        name, batch.category_counts(classified, points), pd.DataFrame(), points,
        batch.summarize(classified, points),
    )


class ClassificationTests(unittest.TestCase):
    def test_strict_threshold_and_overflow(self):
        frame = observations([{"MD": gap} for gap in (0.029999, 0.03, -0.03, 0.5, 0.50001)])
        points = batch.classify_points(frame)
        self.assertEqual(points["Plot Status"].tolist(), ["Hit"] + ["Outside threshold"] * 4)
        self.assertEqual(points["Beyond Display Range"].tolist(), [False] * 4 + [True])
        np.testing.assert_array_equal(points["Nearest Gap"], frame["MD"])
        np.testing.assert_array_equal(points["Display X"], frame["MD"])

    def test_tie_priority_and_missing_categories(self):
        points = batch.classify_points(observations([
            {"MD": 0.01, "NU": -0.01},
            {"MD": 0.01, "MC": -0.01, "Telemetered Net Output": -1.0},
            {"DD": 0.01, "DC": -0.01},
            {"CC": -0.2},
            {},
        ]))
        self.assertEqual(points["Nearest Category"].tolist(), ["NU", "MC", "DD", "CC", ""])
        self.assertEqual(points.iloc[-1]["Exclusion Reason"], "No valid category gap")
        self.assertEqual(points.iloc[0]["Tied Categories"], "MD|NU")

    def test_exclusion_reasons_and_nonfinite_values(self):
        points = batch.classify_points(observations([
            {"MD": 0, batch.NOTE_COLUMN: "ZERO_POWER_RANGE"},
            {"MD": 0, batch.NOTE_COLUMN: "MISSING_TELEMETRY"},
            {"MD": 0, "Telemetered Net Output": np.inf},
            {"MD": np.inf},
            {"MD": True},
        ]))
        self.assertTrue(points["Plot Status"].eq("Excluded").all())
        self.assertTrue(points["Exclusion Reason"].ne("").all())
        self.assertTrue(points["Display Y"].isna().all())

    def test_duplicate_key_includes_resource(self):
        first = observations([{"MD": 0}], "A")
        second = observations([{"MD": 0}], "B")
        points = batch.classify_points(pd.concat([first, second], ignore_index=True))
        self.assertEqual(points["Plot Status"].tolist(), ["Hit", "Hit"])
        duplicated = batch.classify_points(pd.concat([first, first], ignore_index=True))
        self.assertTrue(duplicated["Plot Status"].eq("Excluded").all())

    def test_missing_matched_time_is_retained_as_exclusion(self):
        frame = observations([{"MD": 0, "Hour Start UTC": pd.NaT}])
        frame = batch.validate_frame(frame, "TEST_ESR", "2026-01", "matched")
        self.assertEqual(batch.classify_points(frame).iloc[0]["Plot Status"], "Excluded")

    def test_input_identity_and_local_month(self):
        valid = observations([{"MD": 0}])
        batch.validate_frame(valid, "TEST_ESR", "2026-01", "matched")
        for field, value in [
            ("Resource Name", "WRONG"),
            ("Month", "2026-02"),
            ("Hour Start UTC", pd.Timestamp("2026-01-01 02:00")),
            ("Hour Start UTC", pd.Timestamp("2026-01-01 06:01")),
        ]:
            invalid = valid.copy()
            invalid[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                batch.validate_frame(invalid, "TEST_ESR", "2026-01", "matched")
        with self.assertRaises(ValueError):
            batch.validate_frame(valid.drop(columns=["MD"]), "TEST_ESR", "2026-01", "matched")

    def test_classified_missing_or_duplicate_hours_fail(self):
        frame = observations([{"P_ESS_state": "MD"}, {"P_ESS_state": "NU"}])
        frame.loc[1, "Hour Start UTC"] = frame.loc[0, "Hour Start UTC"]
        with self.assertRaises(ValueError):
            batch.validate_frame(frame, "TEST_ESR", "2026-01", "classified")
        frame.loc[1, "Hour Start UTC"] = pd.NaT
        with self.assertRaises(ValueError):
            batch.validate_frame(frame, "TEST_ESR", "2026-01", "classified")

    def test_pooling_reconciles_and_keeps_all_points(self):
        resources = [
            resource_result("A", [{"MD": 0.01}, {"MC": 0.1}, {}]),
            resource_result("B", [{"MD": -0.02}, {"NU": 0}]),
        ]
        counts, points, audit = batch.pool_results(resources)
        self.assertEqual(len(points), 5)
        self.assertEqual(audit["telemetry_status"], {"Hit": 3, "Outside threshold": 1, "Excluded": 1})
        self.assertEqual(counts["Model Hours"].sum(), 5)
        self.assertEqual(counts["Telemetry Hit Hours"].sum(), 3)
        self.assertEqual(audit["excluded_reasons"], {"No valid category gap": 1})
        pd.testing.assert_frame_equal(points, batch.display_positions(points))
        reversed_counts, reversed_points, _ = batch.pool_results(list(reversed(resources)))
        pd.testing.assert_frame_equal(counts, reversed_counts)
        pd.testing.assert_frame_equal(points, reversed_points)


class CountPanelTests(unittest.TestCase):
    def test_overlapping_bars_preserve_category_counts(self):
        counts = pd.DataFrame({
            "Category": batch.STATES,
            "Model Hours": [0, 5, 10, 3, 12, 0, 7],
            "Telemetry Hit Hours": [0, 5, 3, 10, 0, 8, 7],
            "Telemetry Outside Hours": [0, 2, 1, 0, 3, 2, 0],
        })
        points = batch.classify_points(observations([{"MD": 0}]))
        fig = batch.draw_figure(counts, points, {}, "Bar edge cases", batch.count_ticks([counts]))
        try:
            ax = fig.axes[0]
            for container, column, height in zip(
                ax.containers[:2], ["Model Hours", "Telemetry Hit Hours"], [0.36, 0.22]
            ):
                for index, bar in enumerate(container.patches):
                    self.assertEqual(bar.get_x(), 0)
                    self.assertEqual(bar.get_width(), counts.iloc[index][column])
                    self.assertAlmostEqual(bar.get_y() + bar.get_height() / 2, index)
                    self.assertAlmostEqual(bar.get_height(), height)
            self.assertGreater(ax.get_xlim()[0], ax.get_xlim()[1])
            self.assertEqual(len(ax.texts), 14)
        finally:
            batch.plt.close(fig)

    def test_total_profile_aligns_with_bar_edges_and_preserves_counts(self):
        counts = pd.DataFrame({
            "Category": batch.STATES,
            "Model Hours": [5, 3, 0, 12, 2, 4, 7],
            "Telemetry Hit Hours": [2, 0, 3, 10, 0, 1, 4],
            "Telemetry Outside Hours": [3, 2, 0, 1, 0, 5, 2],
        })
        for table in (counts, counts.assign(**{col: 0 for col in batch.COUNT_COLUMNS})):
            y, width = batch.telemetry_profile(table)
            self.assertEqual(y[0], -0.18)
            self.assertEqual(y[-1], 6.18)
            np.testing.assert_array_equal(width[[0, -1]], [0, 0])
            np.testing.assert_allclose(
                width[np.searchsorted(y, np.arange(len(batch.STATES)))],
                table["Telemetry Hit Hours"] + table["Telemetry Outside Hours"],
                rtol=0, atol=1e-12,
            )
            self.assertTrue((width >= 0).all())
        points = batch.classify_points(observations([{"MD": 0}]))
        fig = batch.draw_figure(counts, points, {}, "Profile boundary", batch.count_ticks([counts]))
        try:
            ax = fig.axes[0]
            profile_y = ax.lines[0].get_ydata()
            bars = ax.containers[0].patches
            self.assertAlmostEqual(profile_y[0], bars[0].get_y())
            self.assertAlmostEqual(profile_y[-1], bars[-1].get_y() + bars[-1].get_height())
            fill = ax.images[0]
            self.assertIsNotNone(fill.get_clip_path())
            self.assertEqual(fill.get_alpha(), 0.28)
            np.testing.assert_allclose(fill.get_array()[0, 0], batch.to_rgba(batch.example.COLORS["MD"])[:3])
            np.testing.assert_allclose(fill.get_array()[-1, 0], batch.to_rgba(batch.example.COLORS["MC"])[:3])
        finally:
            batch.plt.close(fig)


if __name__ == "__main__":
    unittest.main()
