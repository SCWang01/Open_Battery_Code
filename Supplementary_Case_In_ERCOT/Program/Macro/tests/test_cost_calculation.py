"""Regression tests for exact NG fuel-cost and carbon evaluation."""

import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

TEST_DIR = Path(__file__).resolve().parent
MODULE_DIR = (
    TEST_DIR if (TEST_DIR / "cost_calculation.py").is_file() else TEST_DIR.parent
)
sys.path.insert(0, str(MODULE_DIR))

from cost_calculation import CARBON_FACTOR_T_PER_MMBTU, CostStack, GasCostStack


def sample_frame():
    heat = np.array([12.0, 8.0, 10.0])
    return {
        "YEAR": [2026, 2026, 2026],
        "MONTH": [5, 5, 5],
        "capacity": [20.0, 10.0, 30.0],
        "$_per_mwh": [30.0, 10.0, 20.0],
        "mmbtu_per_mwh": heat,
        "carbon_t_per_mwh": heat * CARBON_FACTOR_T_PER_MMBTU,
    }


class GasCostStackTests(unittest.TestCase):
    def test_breakpoints_partial_blocks_and_tail(self):
        stack = GasCostStack.from_frame(sample_frame(), "202605")
        result = stack.evaluate([-5.0, 0.0, 5.0, 10.0, 10.5, 40.0, 60.0, 65.0])
        self.assertEqual(stack.capacity_mw, 60.0)
        np.testing.assert_allclose(result.cost, [0, 0, 50, 100, 110, 700, 1300, 1450])
        np.testing.assert_allclose(
            result.marginal_price, [0, 0, 10, 10, 20, 20, 30, 30]
        )
        np.testing.assert_allclose(result.excess_mw, [0, 0, 0, 0, 0, 0, 0, 5])
        np.testing.assert_allclose(
            result.carbon,
            np.array([0, 0, 40, 80, 85, 380, 620, 680]) * CARBON_FACTOR_T_PER_MMBTU,
        )

    def test_cost_order_controls_carbon_and_ties_preserve_rows(self):
        frame = sample_frame()
        frame["$_per_mwh"] = [20.0, 10.0, 20.0]
        result = GasCostStack.from_frame(frame, "202605").evaluate([15, 30, 40])
        np.testing.assert_allclose(result.cost, [200, 500, 700])
        np.testing.assert_allclose(
            result.carbon, np.array([140, 320, 420]) * CARBON_FACTOR_T_PER_MMBTU
        )

    def test_output_retains_scalar_and_matrix_shapes(self):
        stack = GasCostStack.from_frame(sample_frame(), "202605")
        scalar = stack.evaluate(5.0)
        matrix = stack.evaluate([[0, 5], [10, 65]])
        for name in ("cost", "marginal_price", "carbon", "excess_mw"):
            self.assertEqual(getattr(scalar, name).shape, ())
            self.assertEqual(getattr(matrix, name).shape, (2, 2))
        np.testing.assert_allclose(matrix.cost, [[0, 50], [100, 1450]])

    def test_invalid_stack_rows_are_rejected(self):
        changes = [
            ("YEAR", [2026, 2025, 2026]),
            ("MONTH", [5, 6, 5]),
            ("capacity", [20, 0, 30]),
            ("$_per_mwh", [30, -1, 20]),
            ("mmbtu_per_mwh", [12, -8, 10]),
            ("carbon_t_per_mwh", [0.0, 0.4, 0.5]),
            ("carbon_t_per_mwh", [0.7, 0.4, 0.5]),
            ("capacity", [20, np.nan, 30]),
            ("$_per_mwh", [30, np.inf, 20]),
            ("capacity", [20, "bad", 30]),
        ]
        for column, values in changes:
            with self.subTest(column=column, values=values):
                frame = sample_frame()
                frame[column] = values
                with self.assertRaisesRegex(ValueError, column.replace("$", r"\$")):
                    GasCostStack.from_frame(frame, "202605")

    def test_missing_empty_misaligned_and_wrong_month_inputs(self):
        frame = sample_frame()
        del frame["capacity"]
        with self.assertRaisesRegex(ValueError, "missing columns: capacity"):
            GasCostStack.from_frame(frame, "202605")
        with self.assertRaisesRegex(ValueError, "no supply blocks"):
            GasCostStack.from_frame({name: [] for name in sample_frame()}, "202605")
        frame = sample_frame()
        frame["capacity"] = [10]
        with self.assertRaisesRegex(ValueError, "same number of rows"):
            GasCostStack.from_frame(frame, "202605")
        for month in ("2026-05", "202600", "202613", "000005", 202605):
            with self.subTest(month=month), self.assertRaisesRegex(ValueError, "month"):
                GasCostStack.from_frame(sample_frame(), month)

    def test_invalid_power_and_arithmetic_overflow_are_rejected(self):
        stack = GasCostStack.from_frame(sample_frame(), "202605")
        for values in ([1, np.nan], [np.inf], ["bad"]):
            with self.subTest(values=values), self.assertRaisesRegex(
                ValueError, "power"
            ):
                stack.evaluate(values)
        with self.assertRaisesRegex(ValueError, "non-finite cost or carbon"):
            stack.evaluate(np.finfo(float).max)

    def test_booleans_are_rejected_in_power_and_stack_columns(self):
        stack = GasCostStack.from_frame(sample_frame(), "202605")
        for values in (True, np.bool_(False), [5.0, True], [False, 10]):
            with self.subTest(power=values), self.assertRaisesRegex(
                ValueError, "power.*booleans"
            ):
                stack.evaluate(values)
        for column in sample_frame():
            with self.subTest(column=column):
                frame = sample_frame()
                frame[column] = [True, 1.0, 2.0]
                with self.assertRaisesRegex(ValueError, "booleans"):
                    GasCostStack.from_frame(frame, "202605")

    def test_original_workbook_mutation_cannot_change_stack(self):
        frame = sample_frame()
        stack = GasCostStack.from_frame(frame, "202605")
        frame["capacity"][0] = 2000
        frame["$_per_mwh"][1] = 1000
        self.assertEqual(stack.capacity_mw, 60.0)
        self.assertEqual(float(stack.evaluate(5).cost), 50.0)

    def test_matches_legacy_exact_functions(self):
        reference_path = os.environ.get("ERCOT_LEGACY_COST_MODULE")
        if not reference_path:
            self.skipTest("optional ERCOT_LEGACY_COST_MODULE is not configured")
        legacy_path = Path(reference_path).expanduser().resolve()
        if not legacy_path.is_file():
            self.skipTest("legacy reference cost_calculation.py is not available")
        import pandas as pd

        spec = importlib.util.spec_from_file_location(
            "legacy_cost_reference", legacy_path
        )
        legacy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(legacy)
        frame = pd.DataFrame(sample_frame())
        power = np.array([-1, 0, 5, 10, 10.5, 40, 60, 65])
        result = GasCostStack.from_frame(frame, "202605").evaluate(power)
        with patch.object(legacy.pd, "read_excel", return_value=frame):
            np.testing.assert_allclose(result.cost, legacy.gas_cost(power, 2026, 5))
            np.testing.assert_allclose(
                result.marginal_price, legacy.gas_marginal_price(power, 2026, 5)
            )
            np.testing.assert_allclose(
                result.carbon, legacy.ng_carbon_emission(power, 2026, 5)
            )


class MultiFuelStackTests(unittest.TestCase):
    def mixed_frame(self):
        frame = sample_frame()
        frame["primary_fuel"] = ["NG", "SUB", "LIG"]
        frame["carbon_t_per_mwh"] = [12 * 0.05306, 8 * 0.09717, 10 * 0.09772]
        return frame

    def test_hand_calculated_mixed_merit_order_breakpoints_and_tail(self):
        stack = CostStack.from_frame(self.mixed_frame(), "202605", basis="thermal")
        result = stack.evaluate([0, 5, 10, 25, 40, 60, 65])
        np.testing.assert_allclose(result.cost, [0, 50, 100, 400, 700, 1300, 1450])
        np.testing.assert_allclose(
            result.carbon,
            [
                0,
                40 * 0.09717,
                80 * 0.09717,
                80 * 0.09717 + 150 * 0.09772,
                80 * 0.09717 + 300 * 0.09772,
                80 * 0.09717 + 300 * 0.09772 + 240 * 0.05306,
                80 * 0.09717 + 300 * 0.09772 + 300 * 0.05306,
            ],
        )
        np.testing.assert_array_equal(
            result.marginal_price, [0, 10, 10, 20, 20, 30, 30]
        )
        np.testing.assert_array_equal(result.excess_mw, [0, 0, 0, 0, 0, 0, 5])

    def test_coal_species_and_cost_ties_keep_fuel_alignment(self):
        frame = self.mixed_frame()
        frame["primary_fuel"] = ["LIG", "SUB", "LIG"]
        frame["carbon_t_per_mwh"] = [12 * 0.09772, 8 * 0.09717, 10 * 0.09772]
        frame["$_per_mwh"] = [20, 10, 20]
        result = CostStack.from_frame(frame, "202605", basis="coal").evaluate(15)
        self.assertAlmostEqual(float(result.cost), 200)
        self.assertAlmostEqual(float(result.carbon), 80 * 0.09717 + 60 * 0.09772)

    def test_invalid_basis_missing_unknown_mismatched_and_misaligned_fuels(self):
        for basis in (None, [], "oil", "GAS"):
            with self.subTest(basis=basis), self.assertRaisesRegex(ValueError, "basis"):
                CostStack.from_frame(sample_frame(), "202605", basis=basis)
        for basis in ("coal", "thermal"):
            with self.subTest(basis=basis), self.assertRaisesRegex(
                ValueError, "primary_fuel"
            ):
                CostStack.from_frame(sample_frame(), "202605", basis=basis)
        for fuels in (
            ["NG", "BAD", "LIG"],
            ["NG", None, "LIG"],
            ["NG", True, "LIG"],
            ["NG"],
            [["NG", "SUB", "LIG"]],
        ):
            frame = self.mixed_frame()
            frame["primary_fuel"] = fuels
            with self.subTest(fuels=fuels), self.assertRaisesRegex(
                ValueError, "primary_fuel"
            ):
                CostStack.from_frame(frame, "202605", basis="thermal")
        for basis in ("gas", "coal"):
            with self.subTest(basis=basis), self.assertRaisesRegex(
                ValueError, "primary_fuel"
            ):
                CostStack.from_frame(self.mixed_frame(), "202605", basis=basis)
        with self.assertRaisesRegex(ValueError, "primary_fuel"):
            GasCostStack.from_frame(self.mixed_frame(), "202605")

    def test_gas_factor_cannot_silently_validate_coal_or_mixed_rows(self):
        for basis, fuels in (
            ("coal", ["SUB", "SUB", "LIG"]),
            ("thermal", ["NG", "SUB", "LIG"]),
        ):
            frame = sample_frame()  # All three carbon values use the NG factor.
            frame["primary_fuel"] = fuels
            with self.subTest(basis=basis), self.assertRaisesRegex(
                ValueError, "carbon_t_per_mwh"
            ):
                CostStack.from_frame(frame, "202605", basis=basis)

    def test_legacy_gas_and_explicit_gas_match_for_all_metrics(self):
        frame = sample_frame()
        legacy = GasCostStack.from_frame(frame, "202605").evaluate([0, 10, 55, 70])
        frame["primary_fuel"] = ["NG"] * 3
        current = CostStack.from_frame(frame, "202605", basis="gas").evaluate(
            [0, 10, 55, 70]
        )
        for name in ("cost", "carbon", "marginal_price", "excess_mw"):
            np.testing.assert_array_equal(getattr(legacy, name), getattr(current, name))


if __name__ == "__main__":
    unittest.main()
