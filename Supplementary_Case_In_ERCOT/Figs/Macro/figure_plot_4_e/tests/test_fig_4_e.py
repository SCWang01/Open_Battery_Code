"""Regression checks for aggregate ESR Figure E source and export contracts."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from openpyxl import Workbook


TEST_DIR = Path(__file__).resolve().parent
MODULE_DIR = TEST_DIR.parent if TEST_DIR.name == "tests" else TEST_DIR
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

import Fig_4_e_plot as figure_e


class FigureEDaySourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.write_sources()

    @staticmethod
    def workbook(path, sheet_name, headers, rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        book = Workbook()
        sheet = book.active
        sheet.title = sheet_name
        sheet.append(headers)
        for row in rows:
            sheet.append(row)
        book.save(path)
        book.close()

    def write_sources(self, date="2026-04-07"):
        self.date = date
        self.local, self.utc = figure_e._day_hours(date)
        token = date[:7].replace("-", "")
        month_name = pd.Timestamp(date).month_name()
        self.system_path = (
            self.root
            / "Results/Macro_Results/System_Results"
            / f"Rolling_Result_{token}.xlsx"
        )
        self.curtailment_path = (
            self.root / "Data/Macro/Curtailment" / f"Curtailment_{token}.xlsx"
        )
        self.price_path = self.root / "Data/Macro/Price" / f"Price_{month_name}.xlsx"
        self.actual = np.linspace(-40.0, 80.0, 24)
        self.modeled = self.actual - np.linspace(-20.0, 40.0, 24)
        self.curtailment = np.arange(100.0, 124.0)
        self.price = np.arange(10.0, 34.0)
        self.system_headers = [
            figure_e.LOCAL_TIME,
            figure_e.UTC_TIME,
            "P_ESS_actual",
            "P_ESS",
            "curtailment_proxy_actual",
            "price",
        ]
        self.system_rows = [
            [
                self.local[i].to_pydatetime(),
                self.utc[i].tz_localize(None).to_pydatetime(),
                float(self.actual[i]),
                float(self.modeled[i]),
                float(self.curtailment[i]),
                float(self.price[i]),
            ]
            for i in range(24)
        ]
        self.curtailment_rows = [
            [timestamp.to_pydatetime(), float(value)]
            for timestamp, value in zip(self.local, self.curtailment)
        ]
        self.price_rows = [
            [timestamp.to_pydatetime(), float(value)]
            for timestamp, value in zip(self.local, self.price)
        ]
        self.write_system()
        self.write_curtailment()
        self.write_price()

    def write_system(self, rows=None, headers=None):
        self.workbook(
            self.system_path,
            "Rolling",
            self.system_headers if headers is None else headers,
            self.system_rows if rows is None else rows,
        )

    def write_curtailment(self, rows=None):
        self.workbook(
            self.curtailment_path,
            "Curtailment",
            [figure_e.LOCAL_TIME, figure_e.CURTAILMENT_COLUMN],
            self.curtailment_rows if rows is None else rows,
        )

    def write_price(self, rows=None):
        self.workbook(
            self.price_path,
            "Price",
            [figure_e.LOCAL_TIME, "Official_Price"],
            self.price_rows if rows is None else rows,
        )

    def load(self):
        return figure_e.read_day_data(self.date, self.root)

    def test_exact_fleet_charging_shift_direct_proxy_and_hourly_price(self):
        data = self.load()
        self.assertEqual(data.date, "2026-04-07")
        self.assertTrue(data.local_hours.equals(self.local))
        self.assertEqual(data.utc_hours[0], pd.Timestamp("2026-04-07 05:00", tz="UTC"))
        self.assertEqual(data.utc_hours[-1], pd.Timestamp("2026-04-08 04:00", tz="UTC"))
        np.testing.assert_allclose(data.charging_shift_mw, self.actual - self.modeled)
        np.testing.assert_allclose(data.curtailment_proxy_mw, self.curtailment)
        np.testing.assert_allclose(data.price_usd_per_mwh, self.price)
        self.assertEqual(data.price_usd_per_mwh[0], 10.0)
        self.assertEqual(data.price_usd_per_mwh[-1], 33.0)
        self.assertNotEqual(data.price_usd_per_mwh[0], self.price.mean())

    def test_date_selection_discards_other_days_and_forecast_tail(self):
        before = pd.Timestamp("2026-04-06 23:00").to_pydatetime()
        after = pd.Timestamp("2026-05-01 00:00").to_pydatetime()
        self.write_system(
            [
                [
                    before,
                    pd.Timestamp("2026-04-07 04:00").to_pydatetime(),
                    9999,
                    9999,
                    9999,
                    9999,
                ],
                *self.system_rows,
                [
                    after,
                    pd.Timestamp("2026-05-01 05:00").to_pydatetime(),
                    9999,
                    9999,
                    9999,
                    9999,
                ],
            ]
        )
        self.write_curtailment([[before, 9999], *self.curtailment_rows, [after, 9999]])
        self.write_price([[before, 9999], *self.price_rows, [after, 9999]])
        data = self.load()
        self.assertEqual(len(data.local_hours), 24)
        np.testing.assert_allclose(data.price_usd_per_mwh, self.price)
        np.testing.assert_allclose(data.curtailment_proxy_mw, self.curtailment)

    def test_missing_duplicate_reordered_or_fractional_day_hours_fail(self):
        swapped = [row[:] for row in self.system_rows]
        swapped[5], swapped[6] = swapped[6], swapped[5]
        fractional = [row[:] for row in self.system_rows]
        fractional[5][0] += pd.Timedelta(minutes=15)
        cases = {
            "missing": self.system_rows[:-1],
            "duplicate": [*self.system_rows, self.system_rows[5]],
            "reordered": swapped,
            "fractional": fractional,
        }
        for name, rows in cases.items():
            with self.subTest(case=name):
                self.write_system(rows)
                with self.assertRaisesRegex(
                    ValueError, "24 ordered unique local hours"
                ):
                    self.load()

    def test_explicit_utc_and_independent_sources_must_match(self):
        rows = [row[:] for row in self.system_rows]
        rows[0][1] += pd.Timedelta(hours=1)
        self.write_system(rows)
        with self.assertRaisesRegex(ValueError, "UTC hours disagree"):
            self.load()
        self.write_system()
        for column, label in ((4, "curtailment proxy"), (5, "Official_Price")):
            rows = [row[:] for row in self.system_rows]
            rows[0][column] += 1.0
            self.write_system(rows)
            with self.subTest(field=label):
                with self.assertRaisesRegex(
                    ValueError, f"direct {label} source disagrees"
                ):
                    self.load()

    def test_boolean_nonfinite_missing_and_duplicate_field_values_fail(self):
        for value in (True, "NaN", "inf", "not numeric", None):
            rows = [row[:] for row in self.system_rows]
            rows[0][2] = value
            self.write_system(rows)
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "boolean|finite numeric"):
                    self.load()
        self.write_system(headers=[*self.system_headers[:-1], "price_other"])
        with self.assertRaisesRegex(ValueError, "required column 'price'"):
            self.load()
        headers = [*self.system_headers, "price"]
        self.write_system([row + [row[-1]] for row in self.system_rows], headers)
        with self.assertRaisesRegex(ValueError, "must occur exactly once"):
            self.load()
        self.write_system()
        for writer, original in (
            (self.write_curtailment, self.curtailment_rows),
            (self.write_price, self.price_rows),
        ):
            rows = [row[:] for row in original]
            rows[0][1] = True
            writer(rows)
            with self.assertRaisesRegex(ValueError, "boolean"):
                self.load()
            writer()

    def test_nonnegative_proxy_and_month_specific_paths(self):
        rows = [row[:] for row in self.curtailment_rows]
        rows[0][1] = -1.0
        self.write_curtailment(rows)
        with self.assertRaisesRegex(ValueError, "nonnegative MW"):
            self.load()
        self.write_sources("2026-05-07")
        self.assertEqual(self.load().date, "2026-05-07")
        self.assertEqual(self.price_path.name, "Price_May.xlsx")

    def test_date_contract_rejects_other_years_and_dst_padding(self):
        for date in ("2025-04-07", "2026-07-01", "2026-04-31", "2026-4-7", None):
            with self.subTest(date=date):
                with self.assertRaises(ValueError):
                    figure_e._day_hours(date)
        with self.assertRaisesRegex(ValueError, "24-hour physical day, found 23"):
            figure_e._day_hours("2026-03-08")

    def test_fixed_axes_and_png_only_export_contract(self):
        figure = figure_e.build_figure(self.load())
        try:
            power, price = figure.axes
            self.assertEqual(power.get_ylim(), (-1000.0, 5000.0))
            self.assertEqual(price.get_ylim(), (0.0, 70.0))
            np.testing.assert_array_equal(
                power.get_yticks(), np.arange(-1000, 5001, 1000)
            )
            np.testing.assert_array_equal(price.get_yticks(), np.arange(0, 71, 10))
            np.testing.assert_array_equal(power.get_xticks(), np.arange(24))
            np.testing.assert_allclose(figure.get_size_inches() * 25.4, [180, 64])
            self.assertEqual(
                [text.get_text() for text in figure.legends[0].get_texts()],
                ["Curtailment proxy", "Net shift toward charging", "Hub average price"],
            )
        finally:
            figure_e.plt.close(figure)
        output = self.root / "generated/Fig_4_e_20260407.png"
        with patch.object(figure_e.plt.Figure, "savefig", autospec=True) as save:
            self.assertEqual(
                figure_e.plot_fig_4_e(self.date, output, project_root=self.root), output
            )
            self.assertEqual(save.call_count, 1)
            self.assertEqual(save.call_args.args[1], output)
            self.assertEqual(save.call_args.kwargs["dpi"], 720)
            self.assertIs(save.call_args.kwargs["transparent"], True)
        with self.assertRaisesRegex(ValueError, "Only PNG output"):
            figure_e.plot_fig_4_e(
                self.date, output.with_suffix(".svg"), project_root=self.root
            )

    def test_fixed_axis_limits_refuse_to_hide_other_date_values(self):
        data = self.load()
        for field, value in (
            ("charging_shift_mw", -1001.0),
            ("curtailment_proxy_mw", 5001.0),
            ("price_usd_per_mwh", 71.0),
            ("price_usd_per_mwh", -0.1),
        ):
            values = getattr(data, field).copy()
            values[0] = value
            with self.subTest(field=field, value=value):
                with self.assertRaisesRegex(ValueError, "outside fixed axis limits"):
                    figure_e.build_figure(replace(data, **{field: values}))


if __name__ == "__main__":
    unittest.main()
