"""Regression checks for the five-category CAISO price-width calculation."""

import unittest

from Program.add_dsfunction_state_array import state_width_array


class StateWidthTests(unittest.TestCase):
    def test_near_zero_segments_retain_null_classification_and_width(self):
        segments = [
            [0, 2, -5e-7, 10, 10, 3, 0, 0.5],
            [2, 5, 5e-7, 10, 10, 3, 0, 0.5],
        ]
        array, states, widths = state_width_array(
            str(segments), 2, pcmax=-1540, pdmax=1860
        )
        self.assertEqual(states, ["NU", "NU"])
        self.assertEqual(widths, [0.0, 0.0, 5.0, 0.0, 0.0])
        self.assertEqual(array, "[0,0,5,0,0]")

    def test_no_boundary_unequal_counts_use_the_classifier_categories(self):
        segments = [
            [0, 2, -770, 11, 9, 3, 0, 0.5],
            [2, 5, 930, 11, 9, 3, 0, 0.5],
            [5, 9, -770, 9, 11, 3, 0, 0.5],
            [9, 14, 930, 9, 11, 3, 0, 0.5],
        ]
        _, states, widths = state_width_array(
            str(segments), 2, pcmax=-1540, pdmax=1860
        )
        self.assertEqual(states, ["CC", "DC", "CD", "DD"])
        self.assertEqual(widths, [2.0, 4.0, 0.0, 3.0, 5.0])

    def test_max_rate_segments_remain_excluded_with_equal_mode_counts(self):
        # Include rounded powers within the classifier's max-rate tolerance.
        segments = [
            [0, 2, -1540.0, 10, 10, 3, 0, 0.5],
            [2, 5, 1860.0, 10, 10, 3, 0, 0.5],
            [5, 9, -1539.9998, 10, 10, 3, 0, 0.5],
            [9, 14, 1859.9998, 10, 10, 3, 0, 0.5],
        ]
        array, states, widths = state_width_array(
            str(segments), 2, pcmax=-1540, pdmax=1860
        )
        self.assertEqual(states, ["MC", "MD", "MC", "MD"])
        self.assertEqual(widths, [0.0] * 5)
        self.assertEqual(array, "[0,0,0,0,0]")

    def test_interior_and_soc_boundary_rules_are_preserved(self):
        segments = [
            [0, 2, -770, 10, 10, 3, 0, 0.5],
            [2, 5, 930, 10, 10, 3, 0, 0.5],
            [5, 9, 0, 10, 10, 3, 0, 0.5],
            [9, 14, -770, 10, 10, 3, 0, 0.0],
            [14, 20, 930, 10, 10, 3, 0, 1.0],
        ]
        _, states, widths = state_width_array(
            str(segments), 2, pcmax=-1540, pdmax=1860
        )
        self.assertEqual(states, ["CC", "DD", "NU", "mB", "mB"])
        self.assertEqual(widths, [2.0, 0.0, 4.0, 0.0, 3.0])

    def test_archived_may_4_hour_excludes_both_max_rate_segments(self):
        # May2025 classified-width workbook, Excel row 79 (2025-05-04 05:00).
        segments = [
            [-56.36780504247014, 27.249489918467177, -1540.0,
             10, 10, 3, 0, 0.4743944213696896],
            [27.249489918467177, 30.7335438751729, 0.0,
             9, 10, 5, 0, 0.3862618912492077],
            [30.7335438751729, 31.231265868988004, 1389.85,
             10, 10, 3, 0, 0.29812936112872573],
            [31.231265868988004, 92.45107110824569, 1860.0,
             9, 10, 1, -1, 0.26831642517438203],
        ]
        _, states, widths = state_width_array(
            str(segments), 79, pcmax=-1540, pdmax=1860
        )
        self.assertEqual(states, ["MC", "NU", "DD", "MD"])
        self.assertEqual(widths[:2], [0.0, 0.0])
        self.assertAlmostEqual(widths[2], 3.484053956705724)
        self.assertEqual(widths[3], 0.0)
        self.assertAlmostEqual(widths[4], 0.4977219938151016)


if __name__ == "__main__":
    unittest.main()
