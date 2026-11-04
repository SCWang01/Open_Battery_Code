"""Drawing-contract checks for the six-month ERCOT Figure 4d."""

from pathlib import Path
from contextlib import redirect_stdout
from dataclasses import replace
import io
import sys
import tempfile
import unittest
from unittest.mock import patch

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

TEST_DIR = Path(__file__).resolve().parent
MODULE_DIR = (
    TEST_DIR
    if (TEST_DIR / "plot_reduction_rates_radial.py").is_file()
    else TEST_DIR.parent
)
sys.path.insert(0, str(MODULE_DIR))

from plot_profit_increment_radial import (
    ANNULUS_INNER_RADIUS,
    ANNULUS_OUTER_RADIUS,
    INNER_RADIUS,
    MONTHS,
    PLOT_LIMIT,
    ZERO_RADIUS,
    MetricData,
    StudyRates,
    draw_radial_panel,
)
from plot_reduction_rates_radial import PANELS, create_combined_figure
import plot_reduction_rates_radial as shared
import plot_coal_reduction_rates_radial as coal_entry
import plot_thermal_reduction_rates_radial as thermal_entry


def representative_rates():
    profit = np.array(
        [367.339956, 234.935945, 192.935721, 169.627015, 251.784180, 343.062877]
    )
    reduction = np.array([0.712067, 0.867329, 0.980775, 0.667785, 0.534034, 0.481128])
    return StudyRates(
        profit=MetricData(MONTHS, profit, 250.0),
        carbon=MetricData(MONTHS, reduction, 0.7),
        cost=MetricData(MONTHS, reduction.copy(), 0.7),
    )


class Figure4dContractTests(unittest.TestCase):
    def tearDown(self):
        plt.close("all")

    def test_all_months_have_aligned_bars_and_complete_sixty_degree_annulus(self):
        figure = create_combined_figure(representative_rates())
        self.assertEqual(len(figure.axes), 3)
        np.testing.assert_allclose(figure.get_size_inches() * 25.4, [250, 90])
        for axis in figure.axes:
            self.assertEqual(axis.name, "polar")
            self.assertEqual(len(axis.containers), 2)
            bars, annulus = axis.containers
            self.assertEqual(len(bars), 6)
            self.assertEqual(len(annulus), 6)
            widths = np.array([patch.get_width() for patch in annulus])
            np.testing.assert_allclose(widths, np.pi / 3)
            self.assertAlmostEqual(float(widths.sum()), 2 * np.pi)
            bar_centers = np.array(
                [patch.get_x() + patch.get_width() / 2 for patch in bars]
            )
            annulus_centers = np.array(
                [patch.get_x() + patch.get_width() / 2 for patch in annulus]
            )
            np.testing.assert_allclose(bar_centers, annulus_centers)
            np.testing.assert_allclose(np.diff(bar_centers), np.pi / 3)
            for patch in annulus:
                self.assertAlmostEqual(patch.get_y(), ANNULUS_INNER_RADIUS)
                self.assertAlmostEqual(
                    patch.get_y() + patch.get_height(), ANNULUS_OUTER_RADIUS
                )

    def test_large_actual_profit_rates_remain_encoded_without_radial_clipping(self):
        rates = representative_rates()
        figure = create_combined_figure(rates)
        for axis, spec in zip(figure.axes, PANELS):
            np.testing.assert_allclose(axis.get_ylim(), [0, PLOT_LIMIT])
            bars = axis.containers[0]
            recovered = []
            for patch in bars:
                tip, base = patch.get_y(), patch.get_y() + patch.get_height()
                self.assertGreaterEqual(tip, INNER_RADIUS - 1e-12)
                self.assertAlmostEqual(base, ZERO_RADIUS)
                self.assertLess(base, axis.get_ylim()[1])
                recovered.append(
                    patch.get_height() / (ZERO_RADIUS - INNER_RADIUS) * spec.axis_max
                )
            np.testing.assert_allclose(
                recovered, getattr(rates, spec.key).monthly_percent
            )
        self.assertGreater(rates.profit.monthly_percent.max(), 200)

    def test_percentage_endpoints_preserve_the_same_geometry_across_metric_units(self):
        figure = plt.figure()
        heights = []
        for index, axis_max in enumerate((400.0, 1.2), start=1):
            axis = figure.add_subplot(1, 2, index, projection="polar")
            values = np.array([0, 0.2, 0.4, 0.6, 0.8, 1]) * axis_max
            data = MetricData(MONTHS, values, axis_max / 2)
            draw_radial_panel(
                axis, data, "Rate", "#4C8FD3", "#DCEAF7", axis_max, axis_max / 4
            )
            bars = axis.containers[0]
            self.assertAlmostEqual(bars[0].get_height(), 0)
            self.assertAlmostEqual(bars[-1].get_y(), INNER_RADIUS)
            heights.append([patch.get_height() for patch in bars])
        np.testing.assert_allclose(heights[0], heights[1])

    def test_rendered_titles_and_month_labels_stay_inside_the_declared_canvas(self):
        figure = create_combined_figure(representative_rates())
        figure.canvas.draw()
        renderer = figure.canvas.get_renderer()
        width, height = figure.canvas.get_width_height()
        for axis in figure.axes:
            for text in axis.texts:
                if not text.get_visible() or not text.get_text().strip():
                    continue
                bounds = text.get_window_extent(renderer)
                self.assertGreaterEqual(bounds.x0, -0.5, text.get_text())
                self.assertGreaterEqual(bounds.y0, -0.5, text.get_text())
                self.assertLessEqual(bounds.x1, width + 0.5, text.get_text())
                self.assertLessEqual(bounds.y1, height + 0.5, text.get_text())

    def test_new_bases_use_explicit_titles_scales_and_larger_canvases(self):
        for basis, maximum, interval in (("coal", 2.5, 0.5), ("thermal", 1.2, 0.3)):
            rates = replace(representative_rates(), basis=basis)
            if basis == "coal":
                rates = replace(
                    rates,
                    carbon=MetricData(
                        MONTHS, np.array([1.74, 1.91, 2.38, 1.86, 2.0, 1.62]), 1.89
                    ),
                )
            with self.subTest(basis=basis):
                figure = create_combined_figure(rates)
                np.testing.assert_allclose(figure.get_size_inches() * 25.4, [300, 110])
                for axis, spec in zip(figure.axes, shared.FIGURES[basis].panels):
                    labels = [text.get_text() for text in axis.texts]
                    self.assertIn(spec.title, labels)
                    if spec.key != "profit":
                        self.assertIn(basis.capitalize(), spec.title)
                        self.assertEqual(spec.axis_max, maximum)
                        self.assertEqual(spec.tick_interval, interval)
                        self.assertIn(f"{maximum:g}%", labels)
                        self.assertIn(f"{interval:g}%", labels)
                    recovered = [
                        bar.get_height() / (ZERO_RADIUS - INNER_RADIUS) * spec.axis_max
                        for bar in axis.containers[0]
                    ]
                    np.testing.assert_allclose(
                        recovered, getattr(rates, spec.key).monthly_percent
                    )
                figure.canvas.draw()
                renderer = figure.canvas.get_renderer()
                width, height = figure.canvas.get_width_height()
                titles = []
                for axis, spec in zip(figure.axes, shared.FIGURES[basis].panels):
                    for text in axis.texts:
                        if not text.get_text().strip():
                            continue
                        bounds = text.get_window_extent(renderer)
                        self.assertGreaterEqual(bounds.x0, -0.5, text.get_text())
                        self.assertGreaterEqual(bounds.y0, -0.5, text.get_text())
                        self.assertLessEqual(bounds.x1, width + 0.5, text.get_text())
                        self.assertLessEqual(bounds.y1, height + 0.5, text.get_text())
                        if text.get_text() == spec.title:
                            titles.append(bounds)
                for left, right in zip(titles, titles[1:]):
                    self.assertLess(left.x1, right.x0)

    def test_out_of_range_values_fail_without_leaking_a_partial_figure(self):
        for basis, value in (
            ("gas", 1.21),
            ("coal", 2.51),
            ("thermal", 1.21),
            ("coal", -0.1),
        ):
            rates = replace(
                representative_rates(),
                basis=basis,
                carbon=MetricData(MONTHS, np.full(6, value), value),
            )
            with self.subTest(basis=basis, value=value):
                before = plt.get_fignums()
                with self.assertRaisesRegex(ValueError, "must fit"):
                    create_combined_figure(rates)
                self.assertEqual(plt.get_fignums(), before)

    def test_independent_entry_points_route_defaults_and_explicit_directories(self):
        for entry, basis in ((coal_entry, "coal"), (thermal_entry, "thermal")):
            with self.subTest(basis=basis), patch.object(
                shared,
                "load_study_rates",
                return_value=replace(representative_rates(), basis=basis),
            ) as reader, patch.object(
                shared, "export_figure", return_value=Path("figure.png")
            ) as exporter, redirect_stdout(
                io.StringIO()
            ):
                entry.main()
                reader.assert_called_with(
                    shared.DEFAULT_RESULTS_DIR / basis, basis=basis
                )
                self.assertEqual(
                    exporter.call_args.args[1],
                    shared.DEFAULT_OUTPUT_ROOT / basis / "combined_radial",
                )
                self.assertEqual(exporter.call_args.kwargs["basis"], basis)
                entry.main(Path("custom-input"), Path("custom-output"))
                reader.assert_called_with(Path("custom-input"), basis=basis)
                self.assertEqual(
                    exporter.call_args.args[1], Path("custom-output/combined_radial")
                )

    def test_cli_overrides_are_forwarded_without_fuel_path_duplication(self):
        args = ["figure.py", "--results-dir", "input", "--output-root", "output"]
        with patch.object(sys, "argv", args), patch.object(shared, "main") as runner:
            shared.run_cli("thermal")
        runner.assert_called_once_with(Path("input"), Path("output"), basis="thermal")

    def test_exports_in_one_directory_keep_three_distinct_png_names(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            shared, "PNG_DPI", 72
        ):
            directory = Path(temporary)
            for basis in ("gas", "coal", "thermal"):
                figure = create_combined_figure(
                    replace(representative_rates(), basis=basis)
                )
                path = shared.export_figure(figure, directory, basis=basis)
                self.assertTrue(path.is_file())
                self.assertEqual(path.suffix, ".png")
                self.assertEqual(plt.imread(path).shape[2], 4)
            self.assertEqual(
                {path.name for path in directory.iterdir()},
                {
                    "Fig_4_d_202601_202606.png",
                    "Fig_4_d_coal_202601_202606.png",
                    "Fig_4_d_thermal_202601_202606.png",
                },
            )


if __name__ == "__main__":
    unittest.main()
