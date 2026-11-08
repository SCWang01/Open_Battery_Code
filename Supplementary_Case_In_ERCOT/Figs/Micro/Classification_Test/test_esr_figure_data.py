"""Scientific regression fixtures for ESR state widths and mean-price CIs.

Run directly with ``python -B test_esr_figure_data.py``; fixtures do not read
workbooks or render figures.
"""

import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import esr_figure_data as data


WIDTH_STATES = ("CC", "CD", "NU", "DC", "DD")


def fixture_widths(segments, *, capacity=100.0):
    """Evaluate raw-energy staircase fixtures using fixed signed MW limits."""
    return data.segment_widths(
        json.dumps(segments),
        pcmax=-10.0,
        pdmax=10.0,
        maximum_soc=capacity,
        context="scientific fixture",
    )


class SegmentWidthTests(unittest.TestCase):
    def test_raw_energy_is_normalized_before_marginal_boundary_detection(self):
        # One MWh in a 100 MWh battery is 1%, not the upper SOC boundary.
        expected_span = 3.9153433265
        widths = fixture_widths(
            [[2.0, 2.0 + expected_span, 0.0, 3.0, 3.0, 0.0, 0.0, 1.0]]
        )
        self.assertAlmostEqual(widths["NU"], expected_span, places=11)
        self.assertAlmostEqual(sum(widths.values()), expected_span, places=11)
        self.assertEqual(set(widths), set(WIDTH_STATES))

    def test_lower_and_upper_energy_boundaries_are_excluded(self):
        widths = fixture_widths(
            [
                [0.0, 2.0, 5.0, 3.0, 3.0, 0.0, 0.0, 0.0],
                [2.0, 5.0, -5.0, 3.0, 3.0, 0.0, 0.0, 100.0],
                [5.0, 9.0, 0.0, 3.0, 3.0, 0.0, 0.0, 50.0],
            ]
        )
        self.assertEqual(
            widths, {"CC": 0.0, "CD": 0.0, "NU": 4.0, "DC": 0.0, "DD": 0.0}
        )

    def test_marginal_boundary_tolerance_remains_absolute_mwh(self):
        widths = fixture_widths(
            [
                [0.0, 1.0, 5.0, 3.0, 3.0, 0.0, 0.0, 0.0005],
                [1.0, 3.0, 5.0, 3.0, 3.0, 0.0, 0.0, 0.0015],
                [3.0, 7.0, -5.0, 3.0, 3.0, 0.0, 0.0, 99.9995],
                [7.0, 15.0, -5.0, 3.0, 3.0, 0.0, 0.0, 99.9985],
            ]
        )
        self.assertEqual(widths["CC"], 8.0)
        self.assertEqual(widths["DD"], 2.0)
        self.assertEqual(sum(widths.values()), 10.0)

    def test_boundary_occupancy_excludes_zero_and_inward_power(self):
        for energy in (0.0, 0.001, 99.999, 100.0):
            for power in (-5.0, -0.001, 0.0, 0.001, 5.0):
                with self.subTest(energy=energy, power=power):
                    widths = fixture_widths(
                        [[0.0, 4.0, power, 3.0, 3.0, 0.0, 0.0, energy]]
                    )
                    self.assertEqual(widths, dict.fromkeys(WIDTH_STATES, 0.0))

    def test_boundary_exclusion_precedes_idle_and_max_rate_classification(self):
        widths = fixture_widths(
            [
                [0.0, 2.0, 0.0, 3.0, 3.0, 0.0, 0.0, 0.0],
                [2.0, 5.0, 10.0, 3.0, 3.0, 0.0, 0.0, 100.0],
                [5.0, 9.0, 0.001, 3.0, 3.0, 0.0, 0.0, 50.0],
            ]
        )
        self.assertEqual(widths, {"CC": 0.0, "CD": 0.0, "NU": 4.0, "DC": 0.0, "DD": 0.0})

    def test_equal_mode_counts_preserve_max_rate_exclusion(self):
        # Equal counts must not add maximum-rate segments to panel (a).
        widths = fixture_widths(
            [
                [0.0, 2.5, -10.0, 3.0, 3.0, 0.0, 0.0, 50.0],
                [2.5, 6.5, 10.0, 3.0, 3.0, 0.0, 0.0, 50.0],
            ]
        )
        self.assertEqual(widths, dict.fromkeys(WIDTH_STATES, 0.0))

    def test_rounded_max_rate_segments_remain_excluded(self):
        widths = fixture_widths(
            [
                [0.0, 2.5, -9.9999, 3.0, 3.0, 0.0, 0.0, 50.0],
                [2.5, 6.5, 9.9999, 3.0, 3.0, 0.0, 0.0, 50.0],
            ]
        )
        self.assertEqual(widths, dict.fromkeys(WIDTH_STATES, 0.0))

    def test_equal_mode_counts_still_include_non_max_rate_segments(self):
        widths = fixture_widths(
            [
                [0.0, 2.5, -5.0, 3.0, 3.0, 0.0, 0.0, 50.0],
                [2.5, 6.5, 5.0, 3.0, 3.0, 0.0, 0.0, 50.0],
            ]
        )
        self.assertEqual(
            widths, {"CC": 2.5, "CD": 0.0, "NU": 0.0, "DC": 0.0, "DD": 4.0}
        )

    def test_near_zero_power_retains_null_classification_and_width(self):
        widths = fixture_widths(
            [
                [0.0, 2.5, -5e-7, 3.0, 3.0, 0.0, 0.0, 50.0],
                [2.5, 6.5, 5e-7, 3.0, 3.0, 0.0, 0.0, 50.0],
            ]
        )
        self.assertEqual(
            widths, {"CC": 0.0, "CD": 0.0, "NU": 6.5, "DC": 0.0, "DD": 0.0}
        )

    def test_first_zero_width_sample_preserves_remaining_hour_segments(self):
        widths = fixture_widths(
            [
                [1.0, 1.0, -10.0, 3.0, 3.0, 0.0, 0.0, 50.0],
                [1.0, 4.5, 0.0, 3.0, 3.0, 0.0, 0.0, 50.0],
            ]
        )
        self.assertEqual(widths["NU"], 3.5)
        self.assertEqual(sum(widths.values()), 3.5)

    def test_reversed_price_interval_is_rejected(self):
        with self.assertRaises(ValueError):
            fixture_widths([[4.0, 3.0, 0.0, 3.0, 3.0, 0.0, 0.0, 50.0]])


class PriceConfidenceIntervalTests(unittest.TestCase):
    def test_known_student_90_percent_mean_interval_and_sample_deviation(self):
        # For 10,12,14,16,18: mean=14, sample s=sqrt(10), df=4.
        # The two-sided 90% Student-t interval is [10.98511336,17.01488664].
        summaries = data.summarize_prices(
            {
                hour: [10.0 + hour, 12.0 + hour, 14.0 + hour, 16.0 + hour, 18.0 + hour]
                for hour in range(24)
            }
        )
        self.assertEqual([summary.hour for summary in summaries], list(range(24)))
        for summary in summaries:
            with self.subTest(hour=summary.hour):
                self.assertEqual(summary.sample_size, 5)
                self.assertEqual(summary.mean, 14.0 + summary.hour)
                self.assertAlmostEqual(
                    summary.standard_deviation, 3.1622776601683795, places=12
                )
                self.assertAlmostEqual(
                    summary.ci_low, 10.985113361875355 + summary.hour, places=10
                )
                self.assertAlmostEqual(
                    summary.ci_high, 17.014886638124644 + summary.hour, places=10
                )

    def test_constant_prices_produce_a_collapsed_mean_interval(self):
        summaries = data.summarize_prices({hour: [7.25] * 31 for hour in range(24)})
        self.assertEqual(len(summaries), 24)
        for summary in summaries:
            with self.subTest(hour=summary.hour):
                self.assertEqual(summary.sample_size, 31)
                self.assertEqual(summary.standard_deviation, 0.0)
                self.assertEqual(summary.mean, 7.25)
                self.assertEqual(summary.ci_low, 7.25)
                self.assertEqual(summary.ci_high, 7.25)


if __name__ == "__main__":
    unittest.main()
