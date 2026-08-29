import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

import V5_Case_Study as v5
import distributed_V5_case_study as bidding_entry
import distributed_V5_self_scheduling as distributed_entry
import run_V5_self_scheduling as entry


class EstimatedPriceHorizonTests(unittest.TestCase):
    def test_current_and_future_errors_use_the_confirmed_rates_and_clipping(self):
        estimated, relative_error = v5.build_estimated_price_horizon(
            price_horizon=np.array([100.0, 50.0, -20.0]),
            price_error_z=np.array([1.0, -1.0, 0.5]),
            meanstd=2.0,
            price_min=-100.0,
            price_max=101.0,
        )

        np.testing.assert_allclose(relative_error, [0.02, -0.02, 0.0105])
        np.testing.assert_allclose(estimated, [101.0, 49.0, -20.21])

    def test_mismatched_price_and_noise_lengths_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "same one-dimensional shape"):
            v5.build_estimated_price_horizon(
                price_horizon=np.array([100.0, 110.0]),
                price_error_z=np.array([0.0]),
                meanstd=2.0,
                price_min=0.0,
                price_max=200.0,
            )


class RollingSelfSchedulingTests(unittest.TestCase):
    def test_estimated_prices_drive_dispatch_but_original_lmp_settles_profit(self):
        solver_prices = []

        def fake_solver(*args, **kwargs):
            solver_prices.append(np.asarray(kwargs["price"], dtype=float))
            return [5.0, -3.0][len(solver_prices) - 1]

        with patch.object(v5, "self_schedule_current_power", side_effect=fake_solver):
            result = v5.calculate_profit_self_scheduling(
                N_t=2,
                Nday=1,
                eta=0.95,
                CAP=100.0,
                Smax=1.0,
                Smin=0.0,
                Pdmax=np.array([10.0]),
                Pcmax=np.array([10.0]),
                price=np.array([100.0, 200.0, 300.0]),
                Sinitial=0.8,
                meanstd=2.0,
                price_error_z=np.array([[1.0, 0.0], [-1.0, 0.0]]),
                progress_desc=None,
            )

        self.assertEqual(len(solver_prices), 2)
        self.assertEqual(solver_prices[0][0], 102.0)
        self.assertEqual(solver_prices[1][0], 196.0)
        np.testing.assert_allclose(result.estimated_price, [102.0, 196.0])
        np.testing.assert_allclose(result.price_error, [0.02, -0.02])
        np.testing.assert_allclose(result.power, [5.0, -3.0])
        np.testing.assert_allclose(result.profit, [6000.0, -7200.0])

    def test_self_scheduling_results_directory_is_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            expected = Path(directory) / "Self-Scheduling"
            with patch.object(v5, "SELF_SCHEDULING_RESULTS_DIR", expected):
                actual = v5.ensure_self_scheduling_results_dir()

            self.assertEqual(actual, expected)
            self.assertTrue(expected.is_dir())
            self.assertNotEqual(actual, v5.RESULTS_DIR)

    def test_month_assembly_keeps_controlled_and_passive_power_separate(self):
        controlled = v5.SelfSchedulingDispatchResult(
            profit=np.array([10.0, 20.0]),
            soc=np.array([0.4, 0.3]),
            power=np.array([1.0, 2.0]),
            end_value=1.0,
            estimated_price=np.array([101.0, 198.0]),
            price_error=np.array([0.01, -0.01]),
        )
        passive_result = (
            np.array([80.0, -40.0]),
            np.zeros(2),
            np.array([8.0, -4.0]),
            2.0,
        )
        actual_result = (
            np.array([100.0, -50.0]),
            np.zeros(2),
            np.array([10.0, -5.0]),
            3.0,
        )
        pgas = np.zeros((2, 3))
        costs = np.tile([2.0, 3.0, 4.0], (2, 1))
        absorbed = np.array([1.0, -1.0])
        marginal_price = np.zeros((2, 3))
        carbon = np.tile([5.0, 6.0, 7.0], (2, 1))

        with (
            patch.object(v5, 'N_t', 2),
            patch.object(v5, 'N_day', 1, create=True),
            patch.object(v5, 'year', '2025', create=True),
            patch.object(v5, 'monthnum', '05', create=True),
            patch.object(v5, 'calculate_profit_self_scheduling', return_value=controlled),
            patch.object(
                v5,
                'calculate_profit_actual',
                side_effect=[passive_result, actual_result],
            ),
            patch.object(
                v5,
                'calculate_cost_and_carbon',
                return_value=(
                    pgas,
                    costs,
                    absorbed,
                    np.zeros(2),
                    marginal_price,
                    carbon,
                ),
            ),
        ):
            result = v5.calculate_main_self_scheduling(
                meanstd=2.0,
                price=np.array([100.0, 200.0, 300.0]),
                gas=np.zeros(2),
                battery=np.array([10.0, -5.0]),
                curtailment=np.zeros(2),
                Pdmax=np.array([10.0]),
                Pcmax=np.array([10.0]),
                Smax=1.0,
                Smin=0.0,
                Cap=100.0,
                eta=0.95,
                SINI=0.5,
                price_error_z=np.zeros((2, 2)),
            )

        np.testing.assert_allclose(result.hourly_data['P_ESS'], [9.0, -2.0])
        np.testing.assert_allclose(
            result.hourly_data['P_ESS_controlled'], [1.0, 2.0]
        )
        np.testing.assert_allclose(
            result.hourly_data['P_ESS_passive'], [8.0, -4.0]
        )
        np.testing.assert_allclose(result.hourly_data['profit'], [90.0, -20.0])
        self.assertEqual(result.total_profit, 73.0)
        self.assertEqual(result.total_profit_actual, 53.0)


class SelfSchedulingEntryPointTests(unittest.TestCase):
    def test_default_entry_runs_may_2025_only(self):
        expected = Path('may-summary.csv')
        with (
            patch.object(entry, 'run_may_2025_self_scheduling', return_value=expected) as run_may,
            patch.object(entry, 'run_all_months_self_scheduling') as run_all,
        ):
            actual = entry.main([])

        self.assertEqual(actual, expected)
        run_may.assert_called_once_with()
        run_all.assert_not_called()

    def test_all_months_flag_uses_the_full_period_runner(self):
        expected = Path('all-summary.csv')
        with (
            patch.object(entry, 'run_may_2025_self_scheduling') as run_may,
            patch.object(entry, 'run_all_months_self_scheduling', return_value=expected) as run_all,
        ):
            actual = entry.main(['--all-months'])

        self.assertEqual(actual, expected)
        run_all.assert_called_once_with()
        run_may.assert_not_called()


class DistributedSelfSchedulingEntryPointTests(unittest.TestCase):
    def test_parser_defaults_to_all_months(self):
        args = distributed_entry.parse_args([])

        self.assertEqual(args.months, list(range(len(v5.monthlist))))
        self.assertEqual(args.workers, min(12, distributed_entry.mp.cpu_count()))

    def test_validate_months_sorts_deduplicates_and_rejects_bad_values(self):
        self.assertEqual(
            distributed_entry.validate_months([2, 0, 2, 1], 3),
            [0, 1, 2],
        )
        with self.assertRaisesRegex(ValueError, "out-of-range"):
            distributed_entry.validate_months([-1], 1)
        with self.assertRaisesRegex(ValueError, "at least 1"):
            distributed_entry.validate_months([0], 0)

    def test_run_months_writes_summary_in_requested_month_order(self):
        summaries = {
            0: {'year_month': '202301', 'total_profit': 10},
            2: {'year_month': '202303', 'total_profit': 30},
        }
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / 'Self-Scheduling'
            with (
                patch.object(distributed_entry, 'OUTPUT_DIR', output_dir),
                patch.object(distributed_entry.v5, 'load_monthly_price_error_data'),
                patch.object(distributed_entry, 'mp') as multiprocessing,
            ):
                pool = multiprocessing.Pool.return_value.__enter__.return_value
                pool.imap_unordered.return_value = [
                    (2, summaries[2]),
                    (0, summaries[0]),
                ]
                multiprocessing.cpu_count.return_value = 2
                result = distributed_entry.run_months([2, 0], 2)

            self.assertEqual(
                result,
                output_dir / (
                    f'summary_202301_202303_{v5.COST_MODE}_'
                    f'V5_self_scheduling_k{int(v5.k * 100)}.csv'
                ),
            )
            frame = pd.read_csv(result)
            self.assertEqual(frame['year_month'].tolist(), [202301, 202303])


class DistributedBiddingEntryPointTests(unittest.TestCase):
    def test_each_worker_month_requests_dsfunction_export(self):
        expected = ({'year_month': '202301'}, {'Month': 'January2023'})
        with patch.object(
            bidding_entry.v5,
            'run_one_month',
            return_value=expected,
        ) as run_one_month:
            actual = bidding_entry.run_single_month(0)

        self.assertEqual(actual, (0, *expected))
        run_one_month.assert_called_once_with(
            0,
            export_dsfunctions=True,
            return_detection_counts=True,
        )


if __name__ == "__main__":
    unittest.main()
