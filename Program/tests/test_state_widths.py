"""Regression checks for the five-category CAISO price-width calculation."""

import unittest

from Program.add_dsfunction_state_array import state_width_array


class StateWidthTests(unittest.TestCase):
    def test_idle_at_soc_boundary_is_excluded_as_marginal_boundary(self):
        # Boundary membership takes priority over NU, including solver residue.
        for soc in (0.0, 8.43456249288721e-10, 0.001, 0.999, 1.0):
            for power in (0.0, -3e-5, 3e-5):
                with self.subTest(soc=soc, power=power):
                    segments = [[0, 10, power, 0, 0, 24, 0, soc]]
                    array, states, widths = state_width_array(
                        str(segments), 2, pcmax=-1540, pdmax=1860
                    )
                    self.assertEqual(states, ["mB"])
                    self.assertEqual(widths, [0.0] * 5)
                    self.assertEqual(array, "[0,0,0,0,0]")

    def test_idle_just_inside_soc_interior_retains_null_width(self):
        for soc in (0.0010001, 0.9989999, 0.5):
            with self.subTest(soc=soc):
                segments = [[0, 10, 0, 0, 0, 24, 0, soc]]
                array, states, widths = state_width_array(
                    str(segments), 2, pcmax=-1540, pdmax=1860
                )
                self.assertEqual(states, ["NU"])
                self.assertEqual(widths, [0.0, 0.0, 10.0, 0.0, 0.0])
                self.assertEqual(array, "[0,0,10,0,0]")

    def test_soc_boundary_classification_does_not_depend_on_action_direction(self):
        for soc in (0.0005, 0.9995):
            for power in (-1540, -770, 930, 1860):
                with self.subTest(soc=soc, power=power):
                    segments = [[0, 10, power, 10, 10, 3, 0, soc]]
                    _, states, widths = state_width_array(
                        str(segments), 2, pcmax=-1540, pdmax=1860
                    )
                    self.assertEqual(states, ["mB"])
                    self.assertEqual(widths, [0.0] * 5)

    def test_archived_april_idle_boundary_has_no_null_width(self):
        # April2023 workbook row 129: the zero-power stair stays at empty SOC.
        segments = [
            [72.19542638149156, 205.18465930958712, 0.0,
             0, 0, 24, 0, 8.43456249288721e-10],
        ]
        array, states, widths = state_width_array(
            str(segments), 129, pcmax=-1540, pdmax=1860
        )
        self.assertEqual(states, ["mB"])
        self.assertEqual(widths, [0.0] * 5)
        self.assertEqual(array, "[0,0,0,0,0]")

    def test_zero_width_first_segment_retains_state_without_adding_width(self):
        # A first stair containing one candidate has equal price endpoints.
        # Both included and excluded categories must accept that producer output.
        for power, expected_state in [(-1540, "MC"), (-770, "CC")]:
            with self.subTest(state=expected_state):
                segments = [
                    [-10, -10, power, 5, 0, 0, 1, 0.5],
                    [-10, 0, 0, 0, 0, 24, 0, 0.5],
                    [0, 3, 930, 0, 0, 0, -1, 0.5],
                ]
                array, states, widths = state_width_array(
                    str(segments), 2, pcmax=-1540, pdmax=1860
                )
                self.assertEqual(states, [expected_state, "NU", "DD"])
                self.assertEqual(widths, [0.0, 0.0, 10.0, 0.0, 3.0])
                self.assertEqual(array, "[0,0,10,0,3]")

    def test_negative_width_is_rejected_even_for_excluded_max_rate(self):
        segments = [[0, -1, -1540, 5, 0, 0, 1, 0.5]]
        with self.assertRaisesRegex(ValueError, "interval width"):
            state_width_array(str(segments), 2, pcmax=-1540, pdmax=1860)

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
            [9, 14, 930, 10, 10, 3, 0, 0.0],
            [14, 20, -770, 10, 10, 3, 0, 1.0],
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
