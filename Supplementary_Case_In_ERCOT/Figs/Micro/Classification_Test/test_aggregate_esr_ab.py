"""Regression checks for pooled ESR a/b distributions and their CLI contract.

Run directly with ``python -B test_aggregate_esr_ab.py``. Synthetic members
deliberately have unequal denominators; no classified workbooks are read.
"""

from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
import csv
from dataclasses import replace
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import esr_figure_data as data
from figure_plot_4_a_b import plot_state_distribution_pies as ab
from figure_plot_4_c import plot_hourly_state_counts_price as c
import plot_esr_abc as batch


RESOURCES = ("FIXTURE_A", "FIXTURE_B")


def fixture_member(resource, *, month="202605", counts=None, widths=None, excluded=0):
    """Supply the shared data model without performing a workbook load."""
    state_counts = Counter(dict.fromkeys(data.STATE_ORDER, 0))
    state_counts.update(counts or {"MC": 1})
    width_sums = dict.fromkeys(data.WIDTH_STATE_ORDER, 0.0)
    width_sums.update(widths or {"NU": 1.0})
    valid = sum(state_counts.values())
    hourly_counts = {
        state: [state_counts[state]] + [0] * 23 for state in data.STATE_ORDER
    }
    raw_rows = valid + excluded
    return data.ESRFigureData(
        resource=resource,
        month=month,
        source_path=Path(f"classified_{resource}_1hour_{month}.xlsx"),
        width_sums=width_sums,
        state_counts=state_counts,
        hourly_states=data.StateCountData(
            hourly_counts,
            [valid] + [0] * 23,
            [excluded] + [0] * 23,
            [0] * 24,
            [raw_rows] + [0] * 23,
        ),
        prices=[],
        raw_rows=raw_rows,
        excluded_zero_limit=excluded,
    )


def unequal_members(month="202605"):
    return [
        fixture_member(
            RESOURCES[0],
            month=month,
            counts={"MC": 9, "MD": 1},
            widths={"CC": 9.0, "NU": 1.0},
            excluded=2,
        ),
        fixture_member(
            RESOURCES[1],
            month=month,
            counts={"MC": 1, "MD": 29},
            widths={"CC": 1.0, "NU": 89.0},
            excluded=5,
        ),
    ]


def fixture_hub(month="202605"):
    """Keep hub samples separate from ESR nodal-price fixtures."""
    samples_per_hour = 31 if month == "202605" else 30
    prices = data.summarize_prices(
        {
            hour: [10.0 + hour + day for day in range(samples_per_hour)]
            for hour in range(24)
        }
    )
    return data.HubPriceData(
        month=month,
        source_path=Path("hub_prices.csv"),
        prices=prices,
        physical_hours=24 * samples_per_hour,
    )


class AggregateDistributionsTests(unittest.TestCase):
    def setUp(self):
        self.members = unequal_members()
        self.membership = patch.object(ab, "configured_resources", return_value=RESOURCES)
        self.membership.start()
        self.addCleanup(self.membership.stop)

    def test_counts_and_widths_are_summed_before_normalizing(self):
        aggregate = ab.aggregate_distributions(self.members)
        self.assertEqual(aggregate.resources, RESOURCES)
        self.assertEqual(aggregate.month, "202605")
        self.assertEqual(aggregate.raw_rows, 47)
        self.assertEqual(aggregate.excluded_zero_limit, 7)
        self.assertEqual(aggregate.valid_rows, 40)
        self.assertEqual(aggregate.state_counts["MC"], 10)
        self.assertEqual(aggregate.state_counts["MD"], 30)
        self.assertEqual(aggregate.width_sums["CC"], 10.0)
        self.assertEqual(aggregate.width_sums["NU"], 90.0)

        rows = {(row["panel"], row["state"]): row for row in ab.source_data_rows(aggregate)}
        self.assertAlmostEqual(rows[("a", "CC")]["percentage"], 10.0)
        self.assertAlmostEqual(rows[("b", "MC")]["percentage"], 25.0)
        mean_cc_percentage = 100 * (9 / 10 + 1 / 90) / 2
        mean_mc_percentage = 100 * (9 / 10 + 1 / 30) / 2
        self.assertNotAlmostEqual(rows[("a", "CC")]["percentage"], mean_cc_percentage)
        self.assertNotAlmostEqual(rows[("b", "MC")]["percentage"], mean_mc_percentage)
        for panel in ("a", "b"):
            self.assertAlmostEqual(
                sum(row["percentage"] for row in rows.values() if row["panel"] == panel),
                100.0,
            )

    def test_source_rows_preserve_zero_categories_and_audit_counts(self):
        aggregate = ab.aggregate_distributions(self.members)
        rows = ab.source_data_rows(aggregate)
        expected_keys = {
            "month", "resource_count", "resources", "panel", "state", "value",
            "total", "percentage", "raw_rows", "valid_rows", "excluded_zero_limit",
        }
        self.assertEqual(len(rows), 12)
        self.assertEqual(
            [(row["panel"], row["state"]) for row in rows],
            [("a", state) for state in data.WIDTH_STATE_ORDER]
            + [("b", state) for state in data.STATE_ORDER],
        )
        for row in rows:
            with self.subTest(panel=row["panel"], state=row["state"]):
                self.assertEqual(set(row), expected_keys)
                self.assertEqual(row["month"], "202605")
                self.assertEqual(row["resource_count"], 2)
                self.assertEqual(row["resources"], ";".join(RESOURCES))
                self.assertEqual(row["raw_rows"], 47)
                self.assertEqual(row["valid_rows"], 40)
                self.assertEqual(row["excluded_zero_limit"], 7)
                self.assertEqual(row["total"], 100.0 if row["panel"] == "a" else 40)
                if row["state"] in {"CD", "DC", "DD"}:
                    self.assertEqual(row["value"], 0)
                    self.assertEqual(row["percentage"], 0)

    def test_input_order_does_not_change_configured_resource_order_or_totals(self):
        original = ab.aggregate_distributions(self.members)
        reordered = ab.aggregate_distributions(list(reversed(self.members)))
        self.assertEqual(reordered, original)

    def test_missing_duplicate_extra_or_mixed_month_members_are_rejected(self):
        bad_batches = {
            "empty": [],
            "missing": self.members[:1],
            "duplicate": [self.members[0], self.members[0]],
            "extra": self.members + [fixture_member("UNCONFIGURED")],
            "mixed month": [self.members[0], replace(self.members[1], month="202604")],
        }
        for reason, members in bad_batches.items():
            with self.subTest(reason=reason), self.assertRaises(ValueError):
                ab.aggregate_distributions(members)

    def test_invalid_member_values_and_unreconciled_audits_are_rejected(self):
        first, second = self.members
        bad_members = {
            "negative width": replace(first, width_sums={**first.width_sums, "CD": -1.0}),
            "nonfinite width": replace(first, width_sums={**first.width_sums, "CD": float("nan")}),
            "infinite width": replace(first, width_sums={**first.width_sums, "CD": float("inf")}),
            "missing width category": replace(first, width_sums={"CC": 9.0, "NU": 1.0}),
            "negative count": replace(first, state_counts=Counter({"MC": -1, "MD": 11})),
            "fractional count": replace(first, state_counts=Counter({"MC": 9.5, "MD": 0.5})),
            "unplotted state": replace(first, state_counts=Counter({"MC": 9, "NC": 1})),
            "wrong raw total": replace(first, raw_rows=13),
            "negative exclusions": replace(first, excluded_zero_limit=-1, raw_rows=9),
            "fractional raw total": replace(first, raw_rows=12.5),
            "fractional exclusions": replace(first, raw_rows=12.5, excluded_zero_limit=2.5),
            "no valid counts": replace(first, state_counts=Counter(), raw_rows=2),
        }
        for reason, invalid in bad_members.items():
            with self.subTest(reason=reason), self.assertRaises(ValueError):
                ab.aggregate_distributions([invalid, second])

    def test_boundary_only_member_contributes_counts_but_no_widths(self):
        first, second = self.members
        first = replace(first, width_sums=dict.fromkeys(data.WIDTH_STATE_ORDER, 0.0))
        aggregate = ab.aggregate_distributions([first, second])
        self.assertEqual(aggregate.valid_rows, 40)
        self.assertEqual(aggregate.width_sums, second.width_sums)

    def test_fully_unavailable_member_remains_in_population_and_audit(self):
        first, second = self.members
        first = replace(
            first,
            state_counts=Counter(),
            width_sums=dict.fromkeys(data.WIDTH_STATE_ORDER, 0.0),
            excluded_zero_limit=first.raw_rows,
        )
        aggregate = ab.aggregate_distributions([first, second])
        self.assertEqual(aggregate.resources, RESOURCES)
        self.assertEqual(aggregate.raw_rows, 47)
        self.assertEqual(aggregate.valid_rows, second.valid_rows)
        self.assertEqual(aggregate.excluded_zero_limit, first.raw_rows + second.excluded_zero_limit)

    def test_pooled_figure_still_requires_positive_widths(self):
        members = [
            replace(member, width_sums=dict.fromkeys(data.WIDTH_STATE_ORDER, 0.0))
            for member in self.members
        ]
        with self.assertRaisesRegex(ValueError, "all zero"):
            ab.aggregate_distributions(members)

    def test_plot_export_writes_matching_source_csv(self):
        aggregate = ab.aggregate_distributions(self.members)
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary)

            def save_without_rendering(_figure, path, _title):
                path.write_bytes(b"synthetic PNG export")
                return path

            with patch.object(ab, "build_figure", return_value=object()) as build, \
                patch.object(ab, "save_png", side_effect=save_without_rendering), \
                patch.object(ab.plt, "close"):
                path = ab.plot_aggregate(aggregate, output_dir=output_dir)
            self.assertEqual(path.name, "Fig_4_a_b_ALL_ESR_202605.png")
            build.assert_called_once_with(aggregate.state_counts, aggregate.width_sums, 40)
            self.assertTrue(path.is_file())
            csv_path = path.with_name(f"{path.stem}_source_data.csv")
            with csv_path.open(newline="", encoding="utf-8-sig") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 12)
            pooled_mc = next(row for row in rows if row["panel"] == "b" and row["state"] == "MC")
            self.assertEqual(float(pooled_mc["value"]), 10)
            self.assertEqual(float(pooled_mc["total"]), 40)
            self.assertEqual(float(pooled_mc["percentage"]), 25)
            self.assertEqual(pooled_mc["valid_rows"], "40")


class AggregateCommandLineTests(unittest.TestCase):
    def test_default_and_explicit_month(self):
        parser = data.create_argument_parser("Test aggregate CLI", allow_resource=False)
        self.assertEqual(parser.parse_args([]).month, "202605")
        self.assertEqual(parser.parse_args(["--month", "202604"]).month, "202604")

    def test_invalid_months_are_rejected_by_argument_parser(self):
        parser = data.create_argument_parser("Test aggregate CLI", allow_resource=False)
        for month in (
            "2026-05", "2026051", "202600", "202613", "May2026", "000001", "２０２６０５"
        ):
            with self.subTest(month=month), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    parser.parse_args(["--month", month])
                self.assertEqual(error.exception.code, 2)

    def test_standalone_entrypoint_rejects_resource_selection(self):
        with patch.object(sys, "argv", ["plot_state_distribution_pies.py", "--resource", RESOURCES[0]]), \
            redirect_stderr(io.StringIO()), patch.object(ab, "load_month_data") as load:
            with self.assertRaises(SystemExit) as error:
                ab.main()
        self.assertEqual(error.exception.code, 2)
        load.assert_not_called()

    def test_standalone_uses_all_members_for_selected_month(self):
        members = unequal_members(month="202604")
        by_resource = {member.resource: member for member in members}
        with patch.object(sys, "argv", ["plot_state_distribution_pies.py", "--month", "202604"]), \
            patch.object(ab, "configured_resources", return_value=RESOURCES), \
            patch.object(ab, "load_month_data", side_effect=lambda resource, month: by_resource[resource]) as load, \
            patch.object(ab, "plot_aggregate", return_value=Path("aggregate.png")) as plot, \
            redirect_stdout(io.StringIO()):
            ab.main()
        self.assertEqual(load.call_args_list, [call(resource, "202604") for resource in RESOURCES])
        self.assertEqual(plot.call_count, 1)
        aggregate = plot.call_args.args[0]
        self.assertEqual(aggregate.resources, RESOURCES)
        self.assertEqual(aggregate.month, "202604")
        self.assertEqual(aggregate.valid_rows, 40)

    def test_batch_exports_one_aggregate_ab_and_one_aggregate_c(self):
        members = unequal_members(month="202604")
        by_resource = {member.resource: member for member in members}
        hub = fixture_hub("202604")
        with patch.object(sys, "argv", ["plot_esr_abc.py", "--month", "202604"]), \
            patch.object(batch, "configured_resources", return_value=RESOURCES), \
            patch.object(ab, "configured_resources", return_value=RESOURCES), \
            patch.object(c, "configured_resources", return_value=RESOURCES), \
            patch.object(batch, "load_month_data", side_effect=lambda resource, month: by_resource[resource]) as load, \
            patch.object(batch, "load_hub_price_data", return_value=hub) as load_hub, \
            patch.object(batch, "plot_aggregate", return_value=Path("aggregate.png")) as plot_ab, \
            patch.object(batch, "plot_c", return_value=Path("aggregate_c.png")) as plot_c, \
            redirect_stdout(io.StringIO()):
            batch.main()
        self.assertEqual(load.call_args_list, [call(resource, "202604") for resource in RESOURCES])
        load_hub.assert_called_once_with("202604")
        self.assertEqual(plot_ab.call_count, 1)
        aggregate = plot_ab.call_args.args[0]
        self.assertEqual(aggregate.resources, RESOURCES)
        self.assertEqual(aggregate.state_counts["MC"], 10)
        self.assertEqual(aggregate.state_counts["MD"], 30)
        self.assertEqual(plot_c.call_count, 1)
        aggregate_c = plot_c.call_args.args[0]
        self.assertEqual(aggregate_c.resources, RESOURCES)
        self.assertEqual(aggregate_c.mean_states.counts["MC"][0], 5.0)
        self.assertEqual(aggregate_c.mean_states.counts["MD"][0], 15.0)
        self.assertIs(aggregate_c.hub_prices, hub)

    def test_batch_rejects_resource_selection_before_loading(self):
        with patch.object(sys, "argv", ["plot_esr_abc.py", "--resource", RESOURCES[0]]), \
            patch.object(batch, "load_month_data") as load, \
            patch.object(batch, "load_hub_price_data") as load_hub, \
            redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                batch.main()
        self.assertEqual(error.exception.code, 2)
        load.assert_not_called()
        load_hub.assert_not_called()

    def test_batch_missing_member_exports_no_figures(self):
        first = unequal_members()[0]

        def load_resource(resource, month):
            if resource == RESOURCES[1]:
                raise FileNotFoundError(f"classified_{resource}_1hour_{month}.xlsx")
            return first

        with patch.object(sys, "argv", ["plot_esr_abc.py"]), \
            patch.object(batch, "configured_resources", return_value=RESOURCES), \
            patch.object(batch, "load_month_data", side_effect=load_resource), \
            patch.object(batch, "load_hub_price_data", return_value=fixture_hub()), \
            patch.object(batch, "plot_aggregate") as plot_ab, \
            patch.object(batch, "plot_c") as plot_c, \
            redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                batch.main()
        self.assertEqual(error.exception.code, 2)
        plot_ab.assert_not_called()
        plot_c.assert_not_called()

    def test_batch_missing_hub_exports_neither_figure(self):
        by_resource = {member.resource: member for member in unequal_members()}
        with patch.object(sys, "argv", ["plot_esr_abc.py"]), \
            patch.object(batch, "configured_resources", return_value=RESOURCES), \
            patch.object(ab, "configured_resources", return_value=RESOURCES), \
            patch.object(batch, "load_month_data", side_effect=lambda resource, month: by_resource[resource]), \
            patch.object(batch, "load_hub_price_data", side_effect=FileNotFoundError("hub_prices.csv")), \
            patch.object(batch, "plot_aggregate") as plot_ab, \
            patch.object(batch, "plot_c") as plot_c, \
            redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                batch.main()
        self.assertEqual(error.exception.code, 2)
        plot_ab.assert_not_called()
        plot_c.assert_not_called()

    def test_batch_invalid_hourly_c_data_exports_neither_figure(self):
        members = unequal_members()
        # Monthly a/b data still reconcile, but the hourly C audit does not.
        # The batch must validate C before it exports even the valid a/b plot.
        members[1] = replace(
            members[1], hourly_states=replace(members[1].hourly_states, valid=[0] * 24)
        )
        by_resource = {member.resource: member for member in members}
        with patch.object(sys, "argv", ["plot_esr_abc.py"]), \
            patch.object(batch, "configured_resources", return_value=RESOURCES), \
            patch.object(ab, "configured_resources", return_value=RESOURCES), \
            patch.object(c, "configured_resources", return_value=RESOURCES), \
            patch.object(batch, "load_month_data", side_effect=lambda resource, month: by_resource[resource]), \
            patch.object(batch, "load_hub_price_data", return_value=fixture_hub()), \
            patch.object(batch, "plot_aggregate") as plot_ab, \
            patch.object(batch, "plot_c") as plot_c, \
            redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                batch.main()
        self.assertEqual(error.exception.code, 2)
        plot_ab.assert_not_called()
        plot_c.assert_not_called()


if __name__ == "__main__":
    unittest.main()
