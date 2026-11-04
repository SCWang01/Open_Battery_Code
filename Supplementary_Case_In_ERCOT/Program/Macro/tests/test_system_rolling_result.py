"""Independent system-balance, physical-hour and workbook regression checks."""

from __future__ import annotations

from datetime import datetime
from dataclasses import replace
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from openpyxl import Workbook, load_workbook


TEST_DIR = Path(__file__).resolve().parent
MODULE_DIR = TEST_DIR.parent if TEST_DIR.name == "tests" else TEST_DIR
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

import System_Rolling_Result as system


class BalanceScenarioTests(unittest.TestCase):
    def test_independent_regime_table_and_conservation(self):
        gas = np.array([100, 100, 0, 100, 100, 0, 100, 0, 100, 100.0])
        curt = np.array([50, 50, 50, 50, 50, 0, 0, 0, 50, 50.0])
        delta = np.array([0, 30, 30, 120, -30, -30, -30, 30, 100, -80.0])
        before = [values.copy() for values in (gas, curt, delta)]
        new_gas, new_curt = system.balance_scenario(gas, curt, delta)
        np.testing.assert_array_equal(new_gas, [100, 70, 0, 0, 100, 30, 130, 0, 0, 130])
        np.testing.assert_array_equal(new_curt, [50, 50, 80, 70, 20, 0, 0, 30, 50, 0])
        np.testing.assert_allclose(
            new_gas - new_curt + delta, gas - curt, rtol=0, atol=1e-12
        )
        self.assertTrue((new_gas >= 0).all())
        self.assertTrue((new_curt >= 0).all())
        for found, wanted in zip((gas, curt, delta), before):
            np.testing.assert_array_equal(found, wanted)

    def test_no_change_preserves_simultaneous_generation_and_curtailment(self):
        new_gas, new_curt = system.balance_scenario(
            [[100.0, 0.0], [20.0, 50.0]],
            [[40.0, 15.0], [30.0, 0.0]],
            np.zeros((2, 2)),
        )
        self.assertEqual(new_gas.shape, (2, 2))
        self.assertEqual(new_curt.shape, (2, 2))
        np.testing.assert_array_equal(new_gas, [[100, 0], [20, 50]])
        np.testing.assert_array_equal(new_curt, [[40, 15], [30, 0]])

    def test_negative_base_values_are_rejected_but_signed_delta_is_valid(self):
        for gas, curt in (([-1.0], [0.0]), ([0.0], [-1.0])):
            with self.subTest(gas=gas, curtailment=curt):
                with self.assertRaisesRegex(ValueError, "nonnegative"):
                    system.balance_scenario(gas, curt, [-1.0])
        gas, curt = system.balance_scenario([2.0], [3.0], [-5.0])
        np.testing.assert_array_equal(gas, [4.0])
        np.testing.assert_array_equal(curt, [0.0])

    def test_nonfinite_mixed_boolean_overflow_and_shape_inputs_are_rejected(self):
        for column in range(3):
            for value in (np.nan, np.inf, -np.inf):
                arguments = [[1.0], [1.0], [0.0]]
                arguments[column] = [value]
                with self.subTest(column=column, value=value):
                    with self.assertRaisesRegex(ValueError, "finite"):
                        system.balance_scenario(*arguments)
        with self.assertRaisesRegex(ValueError, "boolean"):
            system.balance_scenario([1.0, True], [0.0, 0.0], [0.0, 0.0])
        with self.assertRaisesRegex(ValueError, "numeric"):
            system.balance_scenario([10**400], [0.0], [0.0])
        with self.assertRaisesRegex(ValueError, "identical shapes"):
            system.balance_scenario([1.0, 2.0], [0.0], [0.0, 0.0])


class ExpectedHoursTests(unittest.TestCase):
    def test_january_through_june_follow_physical_hour_counts(self):
        expected_counts = [744, 672, 743, 720, 744, 720]
        for month, count in zip(system.DEFAULT_MONTHS, expected_counts):
            with self.subTest(month=month):
                index = system.expected_hours(month)
                self.assertEqual(len(index), count)
                self.assertEqual(str(index.tz), "UTC")
                self.assertTrue(index.is_unique)
                self.assertTrue(index.is_monotonic_increasing)
                np.testing.assert_array_equal(
                    np.diff(index.asi8), np.full(count - 1, 3_600_000_000_000)
                )

    def test_march_spring_gap_and_end_exclusive_boundary(self):
        index = system.expected_hours("2026-03")
        self.assertEqual(index[0], pd.Timestamp("2026-03-01 06:00", tz="UTC"))
        self.assertEqual(index[-1], pd.Timestamp("2026-04-01 04:00", tz="UTC"))
        local = index.tz_convert(system.LOCAL_ZONE).tz_localize(None)
        self.assertNotIn(pd.Timestamp("2026-03-08 02:00"), local)
        self.assertEqual(local[-1], pd.Timestamp("2026-03-31 23:00"))

    def test_invalid_month_contract(self):
        for month in (None, "202603", "2026-3", "2026-00", "2026-13"):
            with self.subTest(month=month):
                with self.assertRaises(ValueError):
                    system.expected_hours(month)


class SystemHourlyReaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "hourly.xlsx"
        self.utc = system.expected_hours("2026-03")
        self.local = self.utc.tz_convert(system.LOCAL_ZONE).tz_localize(None)

    def write_source(self, local=None, values=None, utc=None, header="NG_Output_MW"):
        local = self.local if local is None else local
        values = np.arange(len(local), dtype=float) if values is None else values
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "Output"
        headers = [system.LOCAL_TIME, header]
        if utc is not None:
            headers.append(system.UTC_TIME)
        worksheet.append(headers)
        for index, (timestamp, value) in enumerate(zip(local, values)):
            row = [pd.Timestamp(timestamp).to_pydatetime(), value]
            if utc is not None:
                row.append(pd.Timestamp(utc[index]).tz_localize(None).to_pydatetime())
            worksheet.append(row)
        workbook.save(self.path)
        workbook.close()
        return self.path

    def read_source(self):
        return system.read_system_hourly(
            self.path, "Output", ("NG_Output_MW",), "2026-03"
        )

    def test_full_march_is_utc_aligned_and_next_day_tail_is_excluded(self):
        tail = pd.date_range("2026-04-01 00:00", periods=24, freq="h")
        combined = self.local.append(tail)
        self.write_source(combined)
        result = self.read_source()
        self.assertTrue(result.index.equals(self.utc))
        self.assertEqual(result.columns.tolist(), ["NG_Output_MW"])
        np.testing.assert_array_equal(result["NG_Output_MW"], np.arange(743))

    def test_missing_duplicate_reordered_and_nonhourly_inputs_fail(self):
        changed = list(self.local)
        changed[50], changed[51] = changed[51], changed[50]
        fractional = list(self.local)
        fractional[50] += pd.Timedelta(minutes=15)
        cases = {
            "missing": list(self.local.delete(100)),
            "duplicate": list(self.local) + [self.local[100]],
            "reordered": changed,
            "nonhourly": fractional,
        }
        for name, timestamps in cases.items():
            with self.subTest(case=name):
                self.write_source(timestamps)
                with self.assertRaisesRegex(ValueError, "ordered unique"):
                    self.read_source()

    def test_explicit_utc_must_agree_with_local_dates(self):
        self.write_source(utc=self.utc)
        self.assertTrue(self.read_source().index.equals(self.utc))
        incorrect = list(self.utc)
        incorrect[0] += pd.Timedelta(hours=1)
        self.write_source(utc=incorrect)
        with self.assertRaisesRegex(ValueError, "UTC and local dates disagree"):
            self.read_source()

    def test_nonexistent_spring_clock_hour_has_contextual_value_error(self):
        timestamps = list(self.local)
        index = timestamps.index(pd.Timestamp("2026-03-08 03:00"))
        timestamps[index] = pd.Timestamp("2026-03-08 02:00")
        self.write_source(timestamps)
        with self.assertRaisesRegex(ValueError, "invalid hourly timestamps"):
            self.read_source()

    def test_required_numeric_fields_and_negative_price_support(self):
        self.write_source(header="Other")
        with self.assertRaisesRegex(ValueError, "missing columns"):
            self.read_source()
        for value in ("not numeric", "NaN", "inf", True):
            values = list(np.arange(743, dtype=float))
            values[17] = value
            self.write_source(values=values)
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "numeric|boolean"):
                    self.read_source()
        self.write_source(values=np.full(743, -3.25), header="Official_Price")
        prices = system.read_system_hourly(
            self.path, "Output", ("Official_Price",), "2026-03"
        )
        np.testing.assert_array_equal(prices["Official_Price"], np.full(743, -3.25))


class SummaryAndWorkbookTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.hours = system.expected_hours("2026-05")[:2]
        self.micro = SimpleNamespace(
            monthly_income={
                "Actual_Realized_Profit": 200.0,
                "Actual_Rolling_Window_Profit": 250.0,
                "Final_SOC_Value_Realized": 5.0,
                "Final_SOC_Value_Rolling": 8.0,
                "Sum_Realized": 205.0,
                "Sum_Rolling": 258.0,
            },
            control_share=1.0,
            resources=tuple(f"ESR_{index}" for index in range(13)),
        )
        self.stack = SimpleNamespace(capacity_mw=200.0)
        self.rolling = pd.DataFrame(
            {
                system.LOCAL_TIME: self.hours.tz_convert(system.LOCAL_ZONE).tz_localize(
                    None
                ),
                system.UTC_TIME: self.hours.tz_localize(None),
                "price": [-7.123456789012, 3.25],
                "P_coal_actual": [50.0, 60.0],
                "P_ESS_actual": [-10.0, 30.0],
                "P_ESS": [5.0, 25.0],
                "profit_actual": [90.0, 110.0],
                "profit": [100.0, 150.0],
            }
        )
        for suffix, gas, curt, cost, carbon, excess in (
            ("_actual", [100, 120], [30, 5], [1000, 2400], [20, 25], [0, 0]),
            ("", [80, 110], [20, 10], [800, 2200], [15, 20], [0, 10]),
            ("_withoutESS", [110, 140], [40, 25], [1100, 2800], [25, 30], [20, 30]),
        ):
            for name, values in (
                ("P_natural_gas", gas),
                ("curtailment_proxy", curt),
                ("cost", cost),
                ("carbon", carbon),
                ("gas_excess_capacity_mw", excess),
            ):
                self.rolling[f"{name}{suffix}"] = np.asarray(values, dtype=float)
            self.rolling[f"gas_extrapolated{suffix}"] = np.asarray(excess) > 0
        self.summary = system._summary(
            self.rolling, self.micro, self.stack, "2026-05", {}, self.directory
        )
        self.result = system.MonthlyResult("2026-05", self.rolling, self.summary, {})

    def metric(self, name):
        values = self.summary.set_index("Metric").loc[name]
        return values[list(system.SCENARIOS)].tolist()

    def test_summary_scenario_bindings_signed_absorption_and_terminal_income(self):
        expected = {
            "NG_Output": [220.0, 190.0, 250.0],
            "Coal_Output_Fixed": [110.0, 110.0, 110.0],
            "NG_Fuel_Cost_Savings_vs_Actual": [0.0, 400.0, -500.0],
            "NG_CO2_Reduction_vs_Actual": [0.0, 10.0, -10.0],
            "total_absorb": [0.0, 5.0, -30.0],
            "Trading_Profit": [200.0, 250.0, "n.a."],
            "Final_SOC_Value": [5.0, 8.0, "n.a."],
            "total_profit": [205.0, 258.0, "n.a."],
            "Extrapolation_Hours": [0, 1, 2],
            "Extrapolation_Energy": [0.0, 10.0, 50.0],
            "Maximum_Excess_Capacity": [0.0, 10.0, 30.0],
            "ESR_Net_Output": [20.0, 30.0, 0],
        }
        for metric, values in expected.items():
            with self.subTest(metric=metric):
                self.assertEqual(self.metric(metric), values)

    def test_typed_excel_roundtrip_and_numeric_corruption_detection(self):
        path = self.directory / "roundtrip.xlsx"
        system._write_workbook(self.result, path)
        system.verify_workbook(self.result, path)
        workbook = load_workbook(path)
        self.assertEqual(workbook.sheetnames, ["Rolling", "Summary"])
        self.assertEqual(workbook.active.title, "Summary")
        sheet = workbook["Rolling"]
        self.assertEqual(sheet.max_column, len(self.rolling.columns))
        for column in (1, 2):
            self.assertIsInstance(sheet.cell(2, column).value, datetime)
            self.assertTrue(sheet.cell(2, column).is_date)
        bool_column = self.rolling.columns.get_loc("gas_extrapolated") + 1
        self.assertIs(sheet.cell(3, bool_column).value, True)
        sheet.cell(2, 3).value = 987.0
        workbook.save(path)
        workbook.close()
        with self.assertRaisesRegex(ValueError, "numeric mismatch in price"):
            system.verify_workbook(self.result, path)

    def test_input_changed_before_publication_prevents_final_export(self):
        source = self.directory / "source.bin"
        source.write_bytes(b"original input")
        fingerprint = system._sha256(source)
        result = system.MonthlyResult(
            self.result.month, self.rolling, self.summary, {source: fingerprint}
        )
        source.write_bytes(b"changed input")
        destination = self.directory / "output"
        with self.assertRaisesRegex(ValueError, "input changed before publication"):
            system.export_results([result], destination)
        self.assertFalse((destination / "Rolling_Result_202605.xlsx").exists())
        self.assertEqual(list(destination.glob(".*.xlsx")), [])

    def test_all_temporary_workbooks_validate_before_existing_output_is_replaced(self):
        destination = self.directory / "output"
        destination.mkdir()
        first = destination / "Rolling_Result_202605.xlsx"
        first.write_bytes(b"existing output")
        second_result = system.MonthlyResult("2026-06", self.rolling, self.summary, {})
        with patch.object(
            system,
            "verify_workbook",
            side_effect=[None, ValueError("second month failed")],
        ):
            with self.assertRaisesRegex(ValueError, "second month failed"):
                system.export_results([self.result, second_result], destination)
        self.assertEqual(first.read_bytes(), b"existing output")
        self.assertFalse((destination / "Rolling_Result_202606.xlsx").exists())
        self.assertEqual(list(destination.glob(".*.xlsx")), [])


class BuildBasisTests(unittest.TestCase):
    """Exercise complete scenario assembly with controlled source readers."""

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.hours = system.expected_hours("2026-01")
        self.size = len(self.hours)
        actual = np.resize([10, 10, -10, 20, -20, 0.0], self.size)
        delta = np.resize([0, 10, -10, 180, -50, 50.0], self.size)
        self.micro = SimpleNamespace(
            utc_index=self.hours,
            actual_mw=actual,
            modeled_mw=actual + delta,
            actual_profit=np.ones(self.size),
            rolling_profit=np.full(self.size, 2.0),
            monthly_income=dict(
                zip(
                    (
                        "Actual_Realized_Profit",
                        "Actual_Rolling_Window_Profit",
                        "Final_SOC_Value_Realized",
                        "Final_SOC_Value_Rolling",
                        "Sum_Realized",
                        "Sum_Rolling",
                    ),
                    (744.0, 1488.0, 10.0, 20.0, 754.0, 1508.0),
                )
            ),
            control_share=1.0,
            resources=tuple(f"ESR_{i}" for i in range(13)),
            sources=(),
            source_hashes={},
        )
        gas = pd.DataFrame(
            {
                "YEAR": [2026, 2026],
                "MONTH": [1, 1],
                "capacity": [80, 50],
                "$_per_mwh": [10, 20],
                "mmbtu_per_mwh": [8, 12],
                "carbon_t_per_mwh": [8 * 0.05306, 12 * 0.05306],
                "primary_fuel": ["NG", "NG"],
            }
        )
        coal = pd.DataFrame(
            {
                "YEAR": [2026, 2026],
                "MONTH": [1, 1],
                "capacity": [30, 20],
                "$_per_mwh": [5, 15],
                "mmbtu_per_mwh": [10, 12],
                "carbon_t_per_mwh": [10 * 0.09717, 12 * 0.09772],
                "primary_fuel": ["SUB", "LIG"],
            }
        )
        self.frames = {
            "NG": gas,
            "CG": coal,
            "Thermal": pd.concat([gas, coal], ignore_index=True),
        }

        def hourly(path, sheet, columns, month):
            values = {
                "NG_Output_MW": 100,
                "CG_Output_MW": 40,
                "Wind Curtailment Proxy (MW)": 12,
                "Solar Curtailment Proxy (MW)": 8,
                "Total Curtailment Proxy (MW)": 20,
                "Official_Price": -5,
            }
            return pd.DataFrame(
                {column: np.full(self.size, values[column]) for column in columns},
                index=self.hours,
            )

        def cost_frame(path, sheet):
            return self.frames[path.stem.split("_")[1]].copy()

        for name, kwargs in (
            ("read_system_hourly", {"side_effect": hourly}),
            ("_read_excel_objects", {"side_effect": cost_frame}),
            ("load_micro_month", {"return_value": self.micro}),
            ("_sha256", {"return_value": "input-fingerprint"}),
        ):
            patcher = patch.object(system, name, **kwargs)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)

    def test_three_bases_hand_calculated_balance_cost_carbon_and_metadata(self):
        expected = {
            "gas": (
                [100, 90, 100, 0, 130, 50],
                [20, 20, 10, 100, 0, 20],
                1200,
                80 * 8 * 0.05306 + 20 * 12 * 0.05306,
            ),
            "coal": (
                [40, 30, 40, 0, 70, 0],
                [20, 20, 10, 160, 0, 30],
                300,
                30 * 10 * 0.09717 + 10 * 12 * 0.09772,
            ),
            "thermal": (
                [140, 130, 140, 0, 170, 90],
                [20, 20, 10, 60, 0, 20],
                1450,
                30 * 10 * 0.09717
                + 80 * 8 * 0.05306
                + 20 * 12 * 0.09772
                + 10 * 12 * 0.05306,
            ),
        }
        for basis, (generation, curtailment, cost, carbon) in expected.items():
            with self.subTest(basis=basis):
                result = system.build_month(self.root, "2026-01", basis=basis)
                config = system.BASES[basis]
                rows = result.rolling
                np.testing.assert_array_equal(
                    rows[config.output_column][:6], generation
                )
                np.testing.assert_array_equal(
                    rows["curtailment_proxy"][:6], curtailment
                )
                np.testing.assert_allclose(rows["cost_actual"], cost)
                np.testing.assert_allclose(rows["carbon_actual"], carbon)
                for suffix, delta in (
                    ("", self.micro.modeled_mw - self.micro.actual_mw),
                    ("_withoutESS", -self.micro.actual_mw),
                ):
                    residual = (
                        rows[f"{config.output_column}{suffix}"]
                        - rows[f"{config.output_column}_actual"]
                        - rows[f"curtailment_proxy{suffix}"]
                        + rows["curtailment_proxy_actual"]
                        + delta
                    )
                    np.testing.assert_allclose(residual, 0, atol=1e-12)
                summary = result.summary.set_index("Metric")
                self.assertEqual(summary.loc["Basis", "Actual"], basis)
                self.assertEqual(summary.loc["total_profit", "Rolling"], 1508)
                self.assertAlmostEqual(
                    summary.loc[
                        f"{config.label}_Fuel_Cost_Savings_vs_Actual", "Rolling"
                    ],
                    rows.cost_savings.sum(),
                )
                self.assertEqual(
                    summary.loc[f"Field: {basis}_extrapolated", "Unit"], "boolean"
                )
                cost_paths = [
                    path
                    for path in result.source_hashes
                    if path.parent.name.endswith("_cost")
                ]
                self.assertEqual(
                    cost_paths,
                    [
                        self.root
                        / "Data/Macro"
                        / config.cost_directory
                        / f"ERCOT_{config.workbook_prefix}_Final_2026_01.xlsx"
                    ],
                )
                if basis == "coal":
                    for suffix in system.SCENARIOS.values():
                        np.testing.assert_array_equal(
                            rows[f"P_natural_gas{suffix}"], 100
                        )
                    self.assertTrue(rows.coal_extrapolated.iloc[4])
                    self.assertEqual(rows.coal_excess_capacity_mw.iloc[4], 20)
                elif basis == "thermal":
                    np.testing.assert_array_equal(rows.P_natural_gas_actual, 100)
                    np.testing.assert_array_equal(rows.P_coal_actual, 40)
                    self.assertNotIn("P_natural_gas", rows)
                    self.assertNotIn("P_coal", rows)
                    self.assertIn(
                        "pooled baseline",
                        summary.loc["Thermal_Baseline_Method", "Definition"],
                    )
                else:
                    np.testing.assert_array_equal(rows.P_coal_actual, 40)

    def test_mixed_basis_exports_do_not_collide_and_roundtrip(self):
        results = [
            replace(
                system.build_month(self.root, "2026-01", basis=basis), source_hashes={}
            )
            for basis in system.BASES
        ]
        output = self.root / "custom-output"
        paths = system.export_results(results, output)
        self.assertEqual(
            paths,
            [
                output / "Rolling_Result_202601.xlsx",
                output / "coal/Rolling_Result_202601.xlsx",
                output / "thermal/Rolling_Result_202601.xlsx",
            ],
        )
        for result, path in zip(results, paths):
            system.verify_workbook(result, path)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            system.export_results([results[0], results[0]], output)

    def test_invalid_coal_input_does_not_replace_existing_output(self):
        path = self.root / "coal/Rolling_Result_202601.xlsx"
        path.parent.mkdir()
        path.write_bytes(b"existing coal result")
        self.frames["CG"].loc[0, "carbon_t_per_mwh"] = 10 * 0.05306
        args = [
            "System_Rolling_Result.py",
            "--basis",
            "coal",
            "--month",
            "2026-01",
            "--project-root",
            str(self.root),
            "--output-dir",
            str(self.root),
        ]
        with patch.object(sys, "argv", args), self.assertRaisesRegex(
            ValueError, "ERCOT_CG.*carbon_t_per_mwh"
        ):
            system.main()
        self.assertEqual(path.read_bytes(), b"existing coal result")

    def test_invalid_basis_fails_before_sources_are_read(self):
        with self.assertRaisesRegex(ValueError, "basis"):
            system.build_month(self.root, "2026-01", basis="oil")
        self._sha256.assert_not_called()


if __name__ == "__main__":
    unittest.main()
