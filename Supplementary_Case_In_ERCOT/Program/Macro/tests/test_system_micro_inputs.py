"""Boundary checks for read-only Micro-to-Macro input validation."""

from pathlib import Path
import hashlib
import os
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import system_micro_inputs as micro


def _number(frame, column, path):
    values = pd.to_numeric(frame[column], errors="coerce")
    if not np.isfinite(values.to_numpy(dtype=float)).all():
        raise ValueError(f"{path}: non-finite number in {column}")
    return values


COMPARISON = SimpleNamespace(
    _number=_number,
    _utc_times=lambda frame, column, path: pd.to_datetime(
        frame[column], errors="raise", utc=True
    ),
)
RESOURCES = ("ESR_A", "ESR_B")
MONTHS = ("2026-01", "2026-02")


def _income_frame():
    values = {
        "Actual_Realized_Profit": 10.0,
        "Final_SOC_Value_Realized": 2.0,
        "Sum_Realized": 12.0,
        "Actual_Rolling_Window_Profit": 20.0,
        "Final_SOC_Value_Rolling": 3.0,
        "Sum_Rolling": 23.0,
    }
    return pd.DataFrame(
        [
            {"Resource": resource, "Month": month, **values}
            for resource in RESOURCES
            for month in MONTHS
        ]
    )


class ComparisonDetailTests(unittest.TestCase):
    def read_detail(self, frame):
        with patch.object(micro, "_read_cells", return_value=frame.copy()):
            return micro._comparison_detail(
                Path("Comparison_Month.xlsx"), RESOURCES, MONTHS, COMPARISON
            )

    def test_complete_member_month_keys_keep_authoritative_values(self):
        detail = self.read_detail(_income_frame())
        self.assertEqual(len(detail), len(RESOURCES) * len(MONTHS))
        self.assertEqual(detail.loc[("ESR_A", "2026-01"), "Sum_Rolling"], 23.0)

    def test_duplicate_income_key_is_rejected(self):
        frame = _income_frame()
        frame = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)
        with self.assertRaisesRegex(ValueError, "duplicate Resource/Month"):
            self.read_detail(frame)

    def test_missing_configured_income_key_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "configured 4 member-month keys"):
            self.read_detail(_income_frame().iloc[1:])

    def test_unconfigured_income_key_is_rejected(self):
        frame = _income_frame()
        frame.loc[0, "Resource"] = "OTHER_ESR"
        with self.assertRaisesRegex(ValueError, "unexpected"):
            self.read_detail(frame)

    def test_nonfinite_income_is_rejected(self):
        for value in (np.nan, np.inf, -np.inf):
            with self.subTest(value=value):
                frame = _income_frame()
                frame.loc[0, "Final_SOC_Value_Rolling"] = value
                with self.assertRaisesRegex(ValueError, "non-finite"):
                    self.read_detail(frame)

    def test_boolean_income_is_rejected(self):
        frame = _income_frame()
        frame["Final_SOC_Value_Rolling"] = frame["Final_SOC_Value_Rolling"].astype(
            object
        )
        frame.loc[0, "Final_SOC_Value_Rolling"] = True
        with self.assertRaisesRegex(ValueError, "boolean"):
            self.read_detail(frame)

    def test_real_excel_mixed_boolean_income_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "Comparison_Month.xlsx"
            frame = _income_frame().astype(object)
            frame.loc[0, "Final_SOC_Value_Rolling"] = True
            # Keep the numeric total consistent with 1.0 to ensure rejection is
            # specifically because the Excel boolean must not become a dollar.
            frame.loc[0, "Sum_Rolling"] = 21.0
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "Monthly_Detail"
            sheet.append(frame.columns.to_list())
            for row in frame.itertuples(index=False, name=None):
                sheet.append(row)
            workbook.save(path)
            workbook.close()
            with self.assertRaisesRegex(ValueError, "boolean"):
                micro._comparison_detail(path, RESOURCES, MONTHS, COMPARISON)

    def test_trading_plus_terminal_must_equal_existing_total(self):
        for total in ("Sum_Realized", "Sum_Rolling"):
            with self.subTest(total=total):
                frame = _income_frame()
                frame.loc[0, total] += 1.0
                with self.assertRaisesRegex(ValueError, "trading plus terminal"):
                    self.read_detail(frame)


class SourceSnapshotTests(unittest.TestCase):
    def test_changed_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "source.txt"
            path.write_text("original", encoding="utf-8")
            snapshot = micro._SourceSnapshot()
            snapshot.track(path)
            path.write_text("a different file size", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "changed during validation"):
                snapshot.verify()

    def test_disappeared_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "source.txt"
            path.write_text("original", encoding="utf-8")
            snapshot = micro._SourceSnapshot()
            snapshot.track(path)
            path.unlink()
            with self.assertRaisesRegex(ValueError, "disappeared during validation"):
                snapshot.verify()

    def test_unchanged_source_returns_resolved_provenance_path(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "source.txt"
            path.write_text("original", encoding="utf-8")
            snapshot = micro._SourceSnapshot()
            snapshot.track(path)
            self.assertEqual(snapshot.verify(), (path.resolve(),))
            self.assertEqual(
                snapshot.hashes[path.resolve()],
                hashlib.sha256(b"original").hexdigest(),
            )

    def test_hash_rejects_changed_content_with_restored_size_and_time(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "source.txt"
            path.write_bytes(b"original")
            snapshot = micro._SourceSnapshot()
            snapshot.track(path)
            state = path.stat()
            path.write_bytes(b"modified")
            os.utime(path, ns=(state.st_atime_ns, state.st_mtime_ns))
            with self.assertRaisesRegex(
                ValueError, "content changed during validation"
            ):
                snapshot.verify()


class BridgeContributionTests(unittest.TestCase):
    def setUp(self):
        self.expected = pd.date_range("2026-01-01 06:00", periods=2, freq="h", tz="UTC")
        self.power = {"ESR_A": np.array([-2.0, 3.0]), "ESR_B": np.array([1.0, 2.0])}
        self.frame = pd.DataFrame(
            {
                "Interval Start": self.expected.tz_convert(
                    "America/Chicago"
                ).tz_localize(None),
                "ESR_Output_MW": [-1.0, 5.0],
                "Hour Start UTC": self.expected.tz_localize(None),
                "Month": ["2026-01", "2026-01"],
                **self.power,
            }
        )

    def validate_bridge(self):
        with patch.object(micro, "_read_cells", return_value=self.frame.copy()):
            return micro._bridge_frame(
                Path("ESR_Rolling_Output_202601.xlsx"),
                self.expected,
                RESOURCES,
                "2026-01",
                self.power,
                COMPARISON,
            )

    def test_matching_member_contributions_are_accepted(self):
        self.assertIsNone(self.validate_bridge())

    def test_member_contribution_mismatch_is_rejected(self):
        self.frame.loc[1, "ESR_A"] += 1.0
        with self.assertRaisesRegex(ValueError, "ESR_A does not match Micro Rolling"):
            self.validate_bridge()

    def test_total_mismatch_is_rejected(self):
        self.frame.loc[1, "ESR_Output_MW"] += 1.0
        with self.assertRaisesRegex(ValueError, "does not equal member dispatch sum"):
            self.validate_bridge()

    def test_local_utc_disagreement_is_rejected(self):
        self.frame.loc[0, "Interval Start"] += pd.Timedelta(hours=1)
        with self.assertRaisesRegex(ValueError, "local timestamps disagree with UTC"):
            self.validate_bridge()


if __name__ == "__main__":
    unittest.main()
