import unittest

import numpy as np
import pandas as pd

from recalculate_gas_postprocess import (
    MeritOrderStack,
    evaluate_stack,
    recalculate_result_frame,
)


def example_stack():
    capacity = np.array([10.0, 20.0])
    price = np.array([2.0, 4.0])
    carbon = np.array([0.1, 0.2])
    cumulative = np.cumsum(capacity)
    lower = np.array([0.0, 10.0])
    return MeritOrderStack(
        capacity=capacity,
        price=price,
        carbon_intensity=carbon,
        cumulative_capacity=cumulative,
        lower_capacity=lower,
        cumulative_cost=np.array([0.0, 20.0]),
        cumulative_carbon=np.array([0.0, 1.0]),
    )


class RecalculateGasPostprocessTests(unittest.TestCase):
    def test_merit_order_cost_marginal_and_carbon(self):
        cost, marginal, carbon = evaluate_stack(
            np.array([0.0, 5.0, 15.0, 35.0]), example_stack()
        )
        np.testing.assert_allclose(cost, [0.0, 10.0, 40.0, 120.0])
        np.testing.assert_allclose(marginal, [0.0, 2.0, 4.0, 4.0])
        np.testing.assert_allclose(carbon, [0.0, 0.5, 2.0, 6.0])

    def test_recalculation_preserves_optimization_columns(self):
        source = pd.DataFrame(
            {
                "P_natural_gas": [5.0, 15.0],
                "P_natural_gas_actual": [6.0, 14.0],
                "P_ESS_actual": [1.0, -2.0],
                "P_ESS": [3.0, -1.0],
                "SOC_controlled": [0.4, 0.5],
                "cost": [999.0, 999.0],
                "cost_actual": [999.0, 999.0],
                "cost_withoutESS": [999.0, 999.0],
                "carbon": [999.0, 999.0],
                "carbon_actual": [999.0, 999.0],
                "carbon_withoutESS": [999.0, 999.0],
                "marginal_price_gas": [999.0, 999.0],
                "marginal_price_gas_actual": [999.0, 999.0],
                "marginal_price_gas_withoutESS": [999.0, 999.0],
                "total_cost": [999.0, 999.0],
                "total_cost_actual": [999.0, 999.0],
                "total_cost_withoutESS": [999.0, 999.0],
                "total_carbon": [999.0, 999.0],
                "total_carbon_actual": [999.0, 999.0],
                "total_carbon_withoutESS": [999.0, 999.0],
            }
        )
        output, totals, unchanged = recalculate_result_frame(
            source, example_stack(), "synthetic.csv"
        )
        np.testing.assert_array_equal(output["P_ESS"], source["P_ESS"])
        np.testing.assert_array_equal(
            output["SOC_controlled"], source["SOC_controlled"]
        )
        self.assertEqual(unchanged, 5)
        self.assertAlmostEqual(totals["total_cost"], 50.0)


if __name__ == "__main__":
    unittest.main()
