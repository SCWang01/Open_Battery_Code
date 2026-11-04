"""Synthetic XLSX checks for complete-month Hub prices and daily DST samples.

Run directly with ``python -B test_hub_price_data.py``. Fixtures are temporary;
no project input workbook is modified and no figure is rendered.
"""

import calendar
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import esr_figure_data as data


def month_rows(month, *, with_utc=False, price=None):
    """Produce real physical hours, including DST, as ordinary Excel values."""
    start = pd.Timestamp(f"{month[:4]}-{month[4:]}-01", tz="America/Chicago")
    end = start + pd.offsets.MonthBegin(1)
    utc_hours = pd.date_range(start, end, freq="h", inclusive="left").tz_convert("UTC")
    result = []
    for utc in utc_hours:
        local = utc.tz_convert("America/Chicago").tz_localize(None)
        row = [local.to_pydatetime(), price(local, utc) if price else 10.0]
        if with_utc:
            row.append(utc.tz_localize(None).to_pydatetime())
        result.append(row)
    return result


class HubPriceDataTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.price_dir = Path(self.temporary.name)

    def write_price(self, month, rows, *, headers=None, sheet="Price"):
        if headers is None:
            headers = ["Interval Start", "Official_Price"]
            if rows and len(rows[0]) == 3:
                headers.append("Hour Start UTC")
        path = self.price_dir / f"Price_{calendar.month_name[int(month[4:])]}.xlsx"
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = sheet
        worksheet.append(headers)
        for row in rows:
            worksheet.append(row)
        workbook.save(path)
        workbook.close()
        return path

    def load(self, month="202605"):
        return data.load_hub_price_data(month, price_dir=self.price_dir)

    def test_default_month_path_and_known_mean_student_t_interval(self):
        # Daily values 1..31 have mean 16 and sample variance 82 2/3.
        # t(0.95, 30)=1.6972608866 gives the stated 90% mean interval.
        path = self.write_price(
            "202605", month_rows("202605", price=lambda local, _: float(local.day))
        )
        with patch.object(data, "HUB_PRICE_DIR", self.price_dir):
            result = data.load_hub_price_data()
        self.assertIsInstance(result, data.HubPriceData)
        self.assertEqual(result.month, "202605")
        self.assertEqual(result.source_path, path)
        self.assertEqual(result.physical_hours, 744)
        self.assertEqual([summary.hour for summary in result.prices], list(range(24)))
        for summary in result.prices:
            with self.subTest(hour=summary.hour):
                self.assertEqual(summary.sample_size, 31)
                self.assertAlmostEqual(summary.mean, 16.0, places=12)
                self.assertAlmostEqual(summary.standard_deviation, 9.092121131323903, places=12)
                self.assertAlmostEqual(summary.ci_low, 13.228384578307346, places=10)
                self.assertAlmostEqual(summary.ci_high, 18.771615421692654, places=10)

    def test_april_filename_and_constant_negative_prices_are_preserved(self):
        path = self.write_price(
            "202604", month_rows("202604", price=lambda *_: -3.25)
        )
        result = self.load("202604")
        self.assertEqual(result.source_path, path)
        self.assertEqual(path.name, "Price_April.xlsx")
        self.assertEqual(result.physical_hours, 720)
        for summary in result.prices:
            self.assertEqual(summary.sample_size, 30)
            self.assertEqual(summary.mean, -3.25)
            self.assertEqual(summary.ci_low, -3.25)
            self.assertEqual(summary.ci_high, -3.25)

    def test_next_month_tail_is_excluded_even_if_tail_price_is_invalid(self):
        rows = month_rows("202605")
        rows.extend(
            [[pd.Timestamp(f"2026-06-01 {hour:02d}:00").to_pydatetime(), "not an actual price"]
             for hour in range(24)]
        )
        self.write_price("202605", rows)
        result = self.load()
        self.assertEqual(result.physical_hours, 744)
        for summary in result.prices:
            self.assertEqual(summary.sample_size, 31)
            self.assertEqual(summary.mean, 10.0)

    def test_march_absent_clock_hour_is_not_padded(self):
        self.write_price("202603", month_rows("202603"))
        result = self.load("202603")
        self.assertEqual(result.physical_hours, 743)
        for summary in result.prices:
            self.assertEqual(summary.sample_size, 30 if summary.hour == 2 else 31)
            self.assertEqual(summary.mean, 10.0)

    def test_autumn_repeat_requires_explicit_utc(self):
        self.write_price("202611", month_rows("202611"))
        with self.assertRaisesRegex(ValueError, "ambiguous.*Hour Start UTC"):
            self.load("202611")

    def test_autumn_two_physical_hours_form_one_daily_price_sample(self):
        def prices(local, utc):
            if local.day == 1 and local.hour == 1:
                return 0.0 if utc.hour == 6 else 40.0
            return 10.0

        self.write_price("202611", month_rows("202611", with_utc=True, price=prices))
        result = self.load("202611")
        self.assertEqual(result.physical_hours, 721)
        self.assertTrue(all(summary.sample_size == 30 for summary in result.prices))
        repeated = result.prices[1]
        # November 1 contributes (0+40)/2=20; the other 29 dates contribute 10.
        self.assertAlmostEqual(repeated.mean, 10.333333333333334, places=12)
        self.assertAlmostEqual(repeated.standard_deviation, 1.8257418583505538, places=12)

    def test_missing_duplicate_reordered_and_partial_utc_coverage_fail(self):
        rows = month_rows("202605", with_utc=True)
        changed = {
            "missing hour": rows[:100] + rows[101:],
            "duplicate hour": rows[:100] + [rows[99]] + rows[100:],
            "reordered hour": rows[:100] + [rows[101], rows[100]] + rows[102:],
            "partial month": rows[:-1],
        }
        for reason, invalid in changed.items():
            with self.subTest(reason=reason):
                self.write_price("202605", invalid)
                with self.assertRaisesRegex(ValueError, "UTC hour|expected 744 hours"):
                    self.load()

    def test_utc_column_values_must_agree_with_local_times(self):
        rows = month_rows("202605", with_utc=True)
        rows[100] = [rows[100][0], 10.0, rows[101][2]]
        self.write_price("202605", rows)
        with self.assertRaisesRegex(ValueError, "local time disagrees"):
            self.load()

    def test_missing_utc_cell_and_aware_local_dates_fail(self):
        for value in (None, "not a timestamp"):
            with self.subTest(utc=value):
                rows = month_rows("202605", with_utc=True)
                rows[100][2] = value
                self.write_price("202605", rows)
                with self.assertRaisesRegex(ValueError, "UTC.*datetime"):
                    self.load()
        rows = month_rows("202605")
        rows[0][0] = "2026-05-01 00:00:00-05:00"
        self.write_price("202605", rows)
        with self.assertRaisesRegex(ValueError, "naive ERCOT local"):
            self.load()

    def test_nonexistent_spring_clock_hour_fails_instead_of_being_shifted(self):
        rows = month_rows("202603")
        insertion = next(
            index for index, row in enumerate(rows)
            if row[0] == pd.Timestamp("2026-03-08 03:00").to_pydatetime()
        )
        rows.insert(insertion, [pd.Timestamp("2026-03-08 02:00").to_pydatetime(), 10.0])
        self.write_price("202603", rows)
        with self.assertRaisesRegex(ValueError, "nonexistent ERCOT local hour"):
            self.load("202603")

    def test_invalid_actual_prices_are_rejected(self):
        for value in (None, True, "not numeric", "NaN", "inf"):
            with self.subTest(price=value):
                rows = month_rows("202605")
                rows[100][1] = value
                self.write_price("202605", rows)
                with self.assertRaisesRegex(ValueError, "Official_Price.*finite number"):
                    self.load()

    def test_required_and_optional_headers_must_be_unique(self):
        bad_headers = (
            ["wrong time", "Official_Price"],
            ["Interval Start", "wrong price"],
            ["Interval Start", "Official_Price", "Official_Price"],
            ["Interval Start", "Interval Start", "Official_Price"],
            ["Interval Start", "Official_Price", "Hour Start UTC", "Hour Start UTC"],
        )
        for headers in bad_headers:
            with self.subTest(headers=headers):
                self.write_price("202605", month_rows("202605"), headers=headers)
                with self.assertRaisesRegex(ValueError, "header"):
                    self.load()

    def test_missing_price_sheet_and_source_file_fail(self):
        with self.assertRaises(FileNotFoundError):
            self.load()
        self.write_price("202605", month_rows("202605"), sheet="Wrong Sheet")
        with self.assertRaisesRegex(ValueError, "Price worksheet is missing"):
            self.load()


if __name__ == "__main__":
    unittest.main()
