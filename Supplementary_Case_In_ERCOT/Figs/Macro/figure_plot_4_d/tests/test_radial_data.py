"""Validate Figure 4d monthly mapping and the unchanged income valuation."""

from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
from openpyxl import Workbook, load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plot_profit_increment_radial import MONTHS, MONTH_HOURS, load_study_rates


def _write_month(path: Path, code: str, index: int, basis: str | None = None) -> None:
    actual_trading = 100.0 * (index + 1)
    rolling_trading = 2.0 * actual_trading
    actual_carbon = 1000.0 + 100.0 * index
    actual_cost = 5000.0 + 1000.0 * index
    rows = (
        ("Month", f"{code[:4]}-{code[4:]}", f"{code[:4]}-{code[4:]}"),
        ("Real_Hours", MONTH_HOURS[code], MONTH_HOURS[code]),
        ("Control_Share", 1, 1),
        ("Selected_ESR_Count", 13, 13),
        ("Trading_Profit", actual_trading, rolling_trading),
        ("Final_SOC_Value", 10.0, 50.0),
        ("total_profit", actual_trading + 10.0, rolling_trading + 50.0),
        ("total_carbon", actual_carbon, actual_carbon * 0.99),
        ("total_cost", actual_cost, actual_cost * 0.995),
        ("Input_Source", "", ""),
        ("Input_Source", "", ""),
    )
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Summary"
    sheet.append(("Metric", "Actual", "Rolling"))
    for row in rows:
        sheet.append(row)
    if basis is not None:
        sheet.append(("Basis", basis, basis))
    workbook.save(path)
    workbook.close()


class RadialDataTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.results = Path(self.temporary.name)
        for index, month in enumerate(MONTHS):
            _write_month(self.results / f"Rolling_Result_{month}.xlsx", month, index)

    def edit(self, code: str, metric: str, scenario: str, value) -> None:
        path = self.results / f"Rolling_Result_{code}.xlsx"
        workbook = load_workbook(path)
        sheet = workbook["Summary"]
        column = 2 if scenario == "Actual" else 3
        for row in sheet.iter_rows(min_row=2):
            if row[0].value == metric:
                sheet.cell(row[0].row, column, value=value)
                break
        else:
            raise AssertionError(f"fixture does not contain {metric}")
        workbook.save(path)
        workbook.close()

    def test_profit_uses_trading_plus_existing_terminal_value(self):
        data = load_study_rates(self.results)
        self.assertEqual(data.profit.months, MONTHS)
        self.assertAlmostEqual(
            data.profit.monthly_percent[0], (250.0 / 110.0 - 1) * 100
        )
        self.assertNotAlmostEqual(data.profit.monthly_percent[0], 100.0)

    def test_overall_uses_sum_of_amounts_not_mean_of_monthly_rates(self):
        data = load_study_rates(self.results)
        expected = (4500.0 / 2160.0 - 1) * 100
        self.assertAlmostEqual(data.profit.overall_percent, expected)
        self.assertFalse(
            np.isclose(data.profit.overall_percent, data.profit.monthly_percent.mean())
        )

    def test_cost_and_carbon_use_actual_denominators(self):
        data = load_study_rates(self.results)
        np.testing.assert_allclose(data.carbon.monthly_percent, 1.0)
        np.testing.assert_allclose(data.cost.monthly_percent, 0.5)
        self.assertAlmostEqual(data.carbon.overall_percent, 1.0)
        self.assertAlmostEqual(data.cost.overall_percent, 0.5)

    def test_duplicate_required_metric_is_rejected(self):
        path = self.results / "Rolling_Result_202601.xlsx"
        workbook = load_workbook(path)
        workbook["Summary"].append(("total_cost", 5000.0, 4975.0))
        workbook.save(path)
        workbook.close()
        with self.assertRaisesRegex(ValueError, "duplicate required Metric"):
            load_study_rates(self.results)

    def test_terminal_value_cannot_be_dropped_from_exported_total(self):
        self.edit("202601", "total_profit", "Rolling", 200.0)
        with self.assertRaisesRegex(ValueError, "trading plus terminal"):
            load_study_rates(self.results)

    def test_zero_baseline_profit_is_rejected(self):
        for metric in ("Trading_Profit", "Final_SOC_Value", "total_profit"):
            self.edit("202601", metric, "Actual", 0.0)
        with self.assertRaisesRegex(ValueError, "denominator must be positive"):
            load_study_rates(self.results)

    def test_negative_baseline_cost_is_rejected(self):
        self.edit("202601", "total_cost", "Actual", -1.0)
        with self.assertRaisesRegex(ValueError, "denominator must be positive"):
            load_study_rates(self.results)

    def test_nonfinite_summary_number_is_rejected(self):
        self.edit("202601", "total_carbon", "Actual", "inf")
        with self.assertRaisesRegex(ValueError, "finite number"):
            load_study_rates(self.results)

    def test_native_excel_boolean_number_is_rejected(self):
        self.edit("202601", "total_carbon", "Actual", True)
        with self.assertRaisesRegex(ValueError, "boolean"):
            load_study_rates(self.results)

    def test_summary_month_must_match_its_filename(self):
        self.edit("202601", "Month", "Rolling", "2026-02")
        with self.assertRaisesRegex(ValueError, "Month does not match"):
            load_study_rates(self.results)

    def test_march_requires_743_real_hours(self):
        self.edit("202603", "Real_Hours", "Rolling", 744)
        with self.assertRaisesRegex(ValueError, "must equal 743"):
            load_study_rates(self.results)

    def test_control_share_must_remain_one(self):
        self.edit("202601", "Control_Share", "Rolling", 0.2)
        with self.assertRaisesRegex(ValueError, "Control_Share.*must equal 1"):
            load_study_rates(self.results)

    def test_selected_cohort_must_contain_13_resources(self):
        self.edit("202601", "Selected_ESR_Count", "Rolling", 12)
        with self.assertRaisesRegex(ValueError, "Selected_ESR_Count.*must equal 13"):
            load_study_rates(self.results)

    def test_all_six_months_are_required(self):
        (self.results / "Rolling_Result_202606.xlsx").unlink()
        with self.assertRaisesRegex(FileNotFoundError, "202606"):
            load_study_rates(self.results)

    def test_explicit_bases_preserve_rate_formulas_and_carry_identity(self):
        for basis in ("gas", "coal", "thermal"):
            with self.subTest(basis=basis):
                for index, month in enumerate(MONTHS):
                    _write_month(
                        self.results / f"Rolling_Result_{month}.xlsx",
                        month,
                        index,
                        basis,
                    )
                data = load_study_rates(self.results, basis=basis)
                self.assertEqual(data.basis, basis)
                np.testing.assert_allclose(data.carbon.monthly_percent, 1)
                np.testing.assert_allclose(data.cost.monthly_percent, 0.5)
                self.assertAlmostEqual(
                    data.profit.overall_percent, (4500 / 2160 - 1) * 100
                )

    def test_missing_basis_is_accepted_only_for_legacy_gas(self):
        self.assertEqual(load_study_rates(self.results).basis, "gas")
        for basis in ("coal", "thermal"):
            with self.subTest(basis=basis), self.assertRaisesRegex(
                ValueError, "missing required Basis"
            ):
                load_study_rates(self.results, basis=basis)

    def test_wrong_basis_in_last_month_or_one_scenario_is_rejected(self):
        for index, month in enumerate(MONTHS):
            _write_month(
                self.results / f"Rolling_Result_{month}.xlsx", month, index, "coal"
            )
        for scenario, value in (
            ("Actual", "gas"),
            ("Rolling", "thermal"),
            ("Actual", True),
        ):
            with self.subTest(scenario=scenario, value=value):
                self.edit("202606", "Basis", scenario, value)
                with self.assertRaisesRegex(
                    ValueError, "202606.*Basis.*must equal 'coal'"
                ):
                    load_study_rates(self.results, basis="coal")
                self.edit("202606", "Basis", scenario, "coal")
        with self.assertRaisesRegex(ValueError, "Basis.*must equal 'gas'"):
            load_study_rates(self.results)

    def test_duplicate_basis_metadata_is_rejected(self):
        path = self.results / "Rolling_Result_202601.xlsx"
        book = load_workbook(path)
        book["Summary"].append(("Basis", "coal", "coal"))
        book["Summary"].append(("Basis", "coal", "coal"))
        book.save(path)
        book.close()
        with self.assertRaisesRegex(ValueError, "duplicate required Metric 'Basis'"):
            load_study_rates(self.results, basis="coal")

    def test_invalid_basis_fails_before_reading_workbooks(self):
        for basis in (None, "Coal", "oil", []):
            with self.subTest(basis=basis), self.assertRaisesRegex(
                ValueError, "basis must"
            ):
                load_study_rates(self.results / "missing", basis=basis)


if __name__ == "__main__":
    unittest.main()
