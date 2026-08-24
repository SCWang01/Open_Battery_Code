import unittest

import pandas as pd

from build_cc_grouped_stacks import (
    build_cc_rows,
    calculate_combined_rates,
    derive_mmbtu_per_mwh,
    select_non_cc_base_rows,
    select_generators_for_average,
)


class BuildCcGroupedStacksTests(unittest.TestCase):
    def test_combined_rates_sum_amounts_before_dividing(self):
        mcf, heat, cost = calculate_combined_rates(120.0, 118.0, 20.0, 4.0)
        self.assertEqual(mcf, 6.0)
        self.assertEqual(heat, 5.9)
        self.assertEqual(cost, 24.0)

    def test_capacity_rule_adds_one_following_generator(self):
        generators = pd.DataFrame(
            {
                "Nameplate Capacity (MW)": [100.0, 120.0, 140.0],
                "source_order": [0, 1, 2],
            }
        )
        selected = select_generators_for_average(generators, 90.0)
        self.assertEqual(selected["Nameplate Capacity (MW)"].tolist(), [100.0, 120.0])

    def test_heat_intensity_is_derived_directly_and_replaces_stale_values(self):
        base = pd.DataFrame(
            {
                "Elec_MMBtu": [90.0, 120.0],
                "Netgen": [10.0, 20.0],
                "mmbtu_per_mwh": [999.0, 999.0],
            }
        )
        derived = derive_mmbtu_per_mwh(base, "test base")
        self.assertEqual(derived["mmbtu_per_mwh"].tolist(), [9.0, 6.0])
        self.assertEqual(
            derived.columns.tolist(),
            ["Elec_MMBtu", "Netgen", "mmbtu_per_mwh"],
        )

    def test_rerun_excludes_both_raw_and_already_grouped_cc_rows(self):
        base = pd.DataFrame(
            {
                "Reported Prime Mover": ["IC", "CA", "CT", "CC_GROUPED", "GT"],
                "block_type": [
                    "legacy_plant_prime_mover",
                    "",
                    "",
                    "combined_cycle_unit_code",
                    "legacy_plant_prime_mover",
                ],
            }
        )
        retained = select_non_cc_base_rows(base)
        self.assertEqual(retained["Reported Prime Mover"].tolist(), ["IC", "GT"])

    def test_multiple_unit_codes_share_intensity_and_conserve_amounts(self):
        raw = pd.DataFrame(
            {
                "YEAR": [2025, 2025],
                "MONTH": [4, 4],
                "Plant Id": [1, 1],
                "Plant Name": ["Example", "Example"],
                "Reported Prime Mover": ["CA", "CT"],
                "Quantity": [10.0, 90.0],
                "Elec_Quantity": [10.0, 90.0],
                "Tot_MMBtu": [10.0, 90.0],
                "Elec_MMBtu": [10.0, 90.0],
                "Netgen": [4.0, 16.0],
                "Physical Unit Label": ["mcf", "mcf"],
            }
        )
        crosswalk = pd.DataFrame(
            {
                "Plant Code": [1, 1, 1, 1],
                "Generator ID": ["CA1", "CT1", "CA2", "CT2"],
                "Prime Mover": ["CA", "CT", "CA", "CT"],
                "Unit Code": ["U1", "U1", "U2", "U2"],
                "Nameplate Capacity (MW)": [4.0, 8.0, 6.0, 12.0],
                "Minimum Load (MW)": [1.0, 2.0, 1.0, 2.0],
                "source_order": [0, 1, 2, 3],
            }
        )
        columns = list(raw.columns) + [
            "MMBtuPer_Unit",
            "mmbtu_per_mwh",
            "Mcf_per_MWh",
            "average_capacity",
            "capacity",
            "Minimum Load (MW)",
            "$_per_mwh",
            "block_id",
            "block_type",
            "unit_code",
            "member_generator_ids",
            "selected_generator_ids",
            "source_scope",
            "allocation_share",
        ]
        blocks, audit = build_cc_rows(raw, crosswalk, 4.0, columns, 2025, 4)

        self.assertEqual(len(blocks), 2)
        self.assertEqual(set(blocks["Mcf_per_MWh"]), {5.0})
        self.assertEqual(set(blocks["mmbtu_per_mwh"]), {5.0})
        self.assertAlmostEqual(blocks["Netgen"].sum(), 20.0)
        self.assertAlmostEqual(blocks["Elec_MMBtu"].sum(), 100.0)
        self.assertEqual(audit.iloc[0]["status"], "grouped")


if __name__ == "__main__":
    unittest.main()
