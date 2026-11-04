"""Regression checks for ESR mean state counts and the independent hub price.

Run directly with ``python -B test_aggregate_esr_c.py``. Fixtures contain
reconciled per-hour data and do not read workbooks or hub-price input files.
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
from figure_plot_4_c import plot_hourly_state_counts_price as c


RESOURCES = ("FIXTURE_A", "FIXTURE_B")


def fixture_member(resource, *, month="202605", counts=None, exclusions=None):
    """Build coherent hourly and monthly state/audit data for one ESR."""
    hourly_counts = {state: [0] * 24 for state in data.STATE_ORDER}
    for state, by_hour in (counts or {}).items():
        for hour, count in by_hour.items():
            hourly_counts[state][hour] = count
    excluded = [0] * 24
    for hour, count in (exclusions or {}).items():
        excluded[hour] = count
    valid = [sum(hourly_counts[state][hour] for state in data.STATE_ORDER) for hour in range(24)]
    raw = [valid[hour] + excluded[hour] for hour in range(24)]
    return data.ESRFigureData(
        resource=resource,
        month=month,
        source_path=Path(f"classified_{resource}_1hour_{month}.xlsx"),
        width_sums={state: 1.0 for state in data.WIDTH_STATE_ORDER},
        state_counts=Counter({state: sum(hourly_counts[state]) for state in data.STATE_ORDER}),
        hourly_states=data.StateCountData(hourly_counts, valid, excluded, [0] * 24, raw),
        prices=data.summarize_prices({hour: [-100.0 - hour] * 3 for hour in range(24)}),
        raw_rows=sum(raw),
        excluded_zero_limit=sum(excluded),
    )


def fixture_members(month="202605"):
    return [
        fixture_member(
            RESOURCES[0], month=month,
            counts={"MC": {0: 2}, "NU": {1: 2, 2: 1}},
            exclusions={0: 1},
        ),
        fixture_member(
            RESOURCES[1], month=month,
            counts={"MD": {0: 5}, "MC": {1: 3}},
            exclusions={1: 2, 2: 1},
        ),
    ]


def fixture_hub(month="202605"):
    samples_per_hour = 31 if month == "202605" else 30
    return data.HubPriceData(
        month=month,
        source_path=Path("hub_prices.csv"),
        prices=data.summarize_prices(
            {
                hour: [10.0 + hour + day for day in range(samples_per_hour)]
                for hour in range(24)
            }
        ),
        physical_hours=24 * samples_per_hour,
    )


class AggregateFigureDataTests(unittest.TestCase):
    def setUp(self):
        self.members = fixture_members()
        self.hub = fixture_hub()
        self.membership = patch.object(c, "configured_resources", return_value=RESOURCES)
        self.membership.start()
        self.addCleanup(self.membership.stop)

    def test_per_hour_counts_are_pooled_and_divided_by_all_configured_esrs(self):
        aggregate = c.aggregate_figure_data(self.members, self.hub)
        self.assertEqual(aggregate.resources, RESOURCES)
        self.assertEqual(aggregate.month, "202605")
        self.assertEqual(aggregate.raw_rows, 17)
        self.assertEqual(aggregate.valid_rows, 13)
        self.assertEqual(aggregate.excluded_zero_limit, 4)
        self.assertEqual(aggregate.raw_states.counts["MC"][:3], [2, 3, 0])
        self.assertEqual(aggregate.raw_states.counts["MD"][:3], [5, 0, 0])
        self.assertEqual(aggregate.raw_states.counts["NU"][:3], [0, 2, 1])
        self.assertEqual(aggregate.mean_states.counts["MC"][:3], [1.0, 1.5, 0.0])
        self.assertEqual(aggregate.mean_states.valid[:3], [3.5, 2.5, 0.5])
        # At hour 2 one member has only an excluded row. The fixed denominator
        # remains two, so the single valid NU observation produces 0.5.
        self.assertEqual(aggregate.raw_states.excluded_zero_limit[2], 1)
        self.assertEqual(aggregate.mean_states.counts["NU"][2], 0.5)
        self.assertEqual(sum(aggregate.mean_states.valid), 13 / 2)
        for hour in range(24):
            self.assertEqual(
                sum(aggregate.mean_states.counts[state][hour] for state in data.STATE_ORDER),
                aggregate.mean_states.valid[hour],
            )

    def test_hub_summaries_are_kept_independent_of_member_nodal_prices(self):
        aggregate = c.aggregate_figure_data(self.members, self.hub)
        self.assertIs(aggregate.hub_prices, self.hub)
        rows = c.source_data_rows(aggregate)
        self.assertEqual(len(rows), 24)
        self.assertEqual([row["hour"] for row in rows], list(range(24)))
        for row, price in zip(rows, self.hub.prices):
            with self.subTest(hour=price.hour):
                self.assertEqual(row["hub_price_mean"], price.mean)
                self.assertEqual(row["hub_price_sd"], price.standard_deviation)
                self.assertEqual(row["hub_price_n"], price.sample_size)
                self.assertEqual(row["hub_price_ci_low"], price.ci_low)
                self.assertEqual(row["hub_price_ci_high"], price.ci_high)
                self.assertEqual(row["confidence_level"], 0.90)
                self.assertIn("t", str(row["ci_method"]).lower())
                self.assertGreater(row["hub_price_mean"], 0)

    def test_source_rows_include_all_states_raw_audits_and_fractional_means(self):
        aggregate = c.aggregate_figure_data(self.members, self.hub)
        rows = c.source_data_rows(aggregate)
        expected_keys = {
            "month", "resource_count", "resources", "hour", "raw_total",
            "valid_total", "excluded_zero_limit_total", "excluded_nc_total",
            "mean_valid", "hub_price_mean", "hub_price_sd", "hub_price_n",
            "hub_price_ci_low", "hub_price_ci_high", "confidence_level", "ci_method",
        } | {f"{state}_{suffix}" for state in data.STATE_ORDER for suffix in ("total", "mean")}
        for row in rows:
            self.assertEqual(set(row), expected_keys)
            self.assertEqual(row["month"], "202605")
            self.assertEqual(row["resource_count"], 2)
            self.assertEqual(row["resources"], ";".join(RESOURCES))
            self.assertEqual(row["CC_total"], 0)
            self.assertEqual(row["CC_mean"], 0)
            self.assertEqual(row["excluded_nc_total"], 0)
        self.assertEqual(rows[0]["MC_total"], 2)
        self.assertEqual(rows[0]["MC_mean"], 1.0)
        self.assertEqual(rows[2]["NU_total"], 1)
        self.assertEqual(rows[2]["NU_mean"], 0.5)
        self.assertEqual(rows[2]["raw_total"], 2)
        self.assertEqual(rows[2]["valid_total"], 1)
        self.assertEqual(rows[2]["excluded_zero_limit_total"], 1)
        self.assertEqual(sum(row["raw_total"] for row in rows), 17)
        self.assertEqual(sum(row["valid_total"] for row in rows), 13)

    def test_member_input_order_does_not_change_the_aggregate(self):
        self.assertEqual(
            c.aggregate_figure_data(self.members, self.hub),
            c.aggregate_figure_data(list(reversed(self.members)), self.hub),
        )

    def test_missing_duplicate_extra_or_mixed_month_inputs_are_rejected(self):
        invalid_inputs = {
            "empty": ([], self.hub),
            "missing member": (self.members[:1], self.hub),
            "duplicate member": ([self.members[0], self.members[0]], self.hub),
            "extra member": (self.members + [fixture_member("UNCONFIGURED")], self.hub),
            "mixed member month": ([self.members[0], replace(self.members[1], month="202604")], self.hub),
            "mismatched hub month": (self.members, fixture_hub("202604")),
        }
        for reason, (members, hub) in invalid_inputs.items():
            with self.subTest(reason=reason), self.assertRaises(ValueError):
                c.aggregate_figure_data(members, hub)

    def test_invalid_hourly_values_and_monthly_reconciliation_are_rejected(self):
        first, second = self.members
        hourly = first.hourly_states
        invalid_states = {
            "missing state": replace(hourly, counts={"MC": hourly.counts["MC"]}),
            "short count series": replace(hourly, counts={**hourly.counts, "MC": [2]}),
            "negative count": replace(hourly, counts={**hourly.counts, "MC": [-1] + [0] * 23}),
            "fractional count": replace(hourly, counts={**hourly.counts, "MC": [2.5] + [0] * 23}),
            "short audit series": replace(hourly, raw=[3]),
            "negative audit": replace(hourly, excluded_zero_limit=[-1] + [0] * 23),
            "fractional audit": replace(hourly, raw=[3.5] + hourly.raw[1:]),
            "wrong hourly valid": replace(hourly, valid=[3] + hourly.valid[1:]),
            "wrong hourly raw": replace(hourly, raw=[4] + hourly.raw[1:]),
        }
        invalid_members = {
            reason: replace(first, hourly_states=invalid) for reason, invalid in invalid_states.items()
        }
        invalid_members.update({
            "wrong monthly counts": replace(first, state_counts=Counter({"MC": 3, "NU": 2})),
            "wrong monthly raw": replace(first, raw_rows=7),
            "wrong monthly exclusions": replace(first, excluded_zero_limit=2),
        })
        for reason, invalid in invalid_members.items():
            with self.subTest(reason=reason), self.assertRaises(ValueError):
                c.aggregate_figure_data([invalid, second], self.hub)

    def test_export_passes_mean_counts_and_hub_prices_and_writes_24_csv_rows(self):
        aggregate = c.aggregate_figure_data(self.members, self.hub)
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary)

            def save_without_rendering(_figure, path, _title):
                path.write_bytes(b"synthetic PNG export")
                return path

            with patch.object(c, "build_figure", return_value=object()) as build, \
                patch.object(c, "save_png", side_effect=save_without_rendering), \
                patch.object(c.plt, "close"):
                path = c.plot_aggregate(aggregate, output_dir=output_dir)
            self.assertEqual(path.name, "Fig_4_c_ALL_ESR_202605.png")
            build.assert_called_once_with(aggregate.mean_states, self.hub.prices)
            self.assertTrue(path.is_file())
            csv_path = path.with_name(f"{path.stem}_source_data.csv")
            with csv_path.open(newline="", encoding="utf-8-sig") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 24)
            self.assertEqual(float(rows[2]["NU_mean"]), 0.5)
            self.assertEqual(float(rows[2]["NU_total"]), 1)
            self.assertEqual(float(rows[0]["hub_price_mean"]), self.hub.prices[0].mean)

    def test_stacked_bar_heights_preserve_fractional_means(self):
        aggregate = c.aggregate_figure_data(self.members, self.hub)
        figure = c.build_figure(aggregate.mean_states, self.hub.prices)
        try:
            count_axis, price_axis = figure.axes
            for state, container in zip(data.STATE_ORDER, count_axis.containers):
                for hour, bar in enumerate(container.patches):
                    self.assertEqual(bar.get_height(), aggregate.mean_states.counts[state][hour])
            self.assertIn("mean", count_axis.get_ylabel().lower())
            self.assertIn("esr", count_axis.get_ylabel().lower())
            self.assertEqual(list(price_axis.lines[0].get_ydata()), [summary.mean for summary in self.hub.prices])
        finally:
            c.plt.close(figure)


class AggregateFigureCommandLineTests(unittest.TestCase):
    def test_default_and_explicit_month_load_all_esrs_and_one_hub(self):
        for arguments, month in (([], "202605"), (["--month", "202604"], "202604")):
            with self.subTest(month=month):
                members = fixture_members(month)
                by_resource = {member.resource: member for member in members}
                hub = fixture_hub(month)
                with patch.object(sys, "argv", ["plot_hourly_state_counts_price.py"] + arguments), \
                    patch.object(c, "configured_resources", return_value=RESOURCES), \
                    patch.object(c, "load_month_data", side_effect=lambda resource, selected_month: by_resource[resource]) as load, \
                    patch.object(c, "load_hub_price_data", return_value=hub) as load_hub, \
                    patch.object(c, "plot_aggregate", return_value=Path("aggregate_c.png")) as plot, \
                    redirect_stdout(io.StringIO()):
                    c.main()
                self.assertEqual(load.call_args_list, [call(resource, month) for resource in RESOURCES])
                load_hub.assert_called_once_with(month)
                self.assertEqual(plot.call_count, 1)
                aggregate = plot.call_args.args[0]
                self.assertEqual(aggregate.month, month)
                self.assertEqual(aggregate.resources, RESOURCES)
                self.assertIs(aggregate.hub_prices, hub)

    def test_resource_selection_is_rejected_before_loading(self):
        with patch.object(sys, "argv", ["plot_hourly_state_counts_price.py", "--resource", RESOURCES[0]]), \
            patch.object(c, "load_month_data") as load, \
            patch.object(c, "load_hub_price_data") as load_hub, \
            redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                c.main()
        self.assertEqual(error.exception.code, 2)
        load.assert_not_called()
        load_hub.assert_not_called()

    def test_missing_hub_or_malformed_member_exports_no_figure(self):
        members = fixture_members()
        bad_hourly = replace(members[1].hourly_states, valid=[0] * 24)
        for reason, member_data, hub_error in (
            ("missing hub", members, FileNotFoundError("hub_prices.csv")),
            ("invalid hourly counts", [members[0], replace(members[1], hourly_states=bad_hourly)], None),
        ):
            with self.subTest(reason=reason):
                by_resource = {member.resource: member for member in member_data}
                with patch.object(sys, "argv", ["plot_hourly_state_counts_price.py"]), \
                    patch.object(c, "configured_resources", return_value=RESOURCES), \
                    patch.object(c, "load_month_data", side_effect=lambda resource, month: by_resource[resource]), \
                    patch.object(c, "load_hub_price_data", return_value=fixture_hub(), side_effect=hub_error), \
                    patch.object(c, "plot_aggregate") as plot, \
                    redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as error:
                        c.main()
                self.assertEqual(error.exception.code, 2)
                plot.assert_not_called()


if __name__ == "__main__":
    unittest.main()
