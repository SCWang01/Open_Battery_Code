# CA/CT combined-cycle grouping

`build_cc_grouped_stacks.py` is the reproducible final assembly step for the
canonical 2023--2025 natural-gas merit-order stacks in `data/ng_cost`.

## Data scope

- EIA-923 `Month_Agg/CAISO_NG_YYYY_MM.xlsx` supplies monthly plant/prime-mover
  fuel consumption and net generation.
- `Month_Agg_Clear_V2/CAISO_NG_Final_YYYY_MM.xlsx` supplies the ungrouped
  non-CA/CT base rows. This directory is an immutable input to the grouping
  step; the already-grouped `data/ng_cost` directory is never used as its own
  base.
- The matching annual EIA-860 `Data/3_1_Generator_YYYY.xlsx` supplies generator
  capacity and `Unit Code` membership.
- `Plant Code + Unit Code` defines a combined-cycle block with identifier
  `CC:{Plant Code}:{Unit Code}`.

EIA-923 does not identify the Unit Code receiving plant-level fuel. Therefore,
CA and CT extensive quantities are summed at plant-month level before any
ratio is calculated. If a plant has multiple Unit Codes, their capacities stay
separate while they share the labelled plant-level EIA-923 intensity. Capacity
shares are used only to keep extensive output columns additive; they do not
claim measured Unit-Code-level fuel allocation.

## Capacity rule

For CA and CT separately, annual EIA-860 generators are retained in source
order until their cumulative nameplate capacity covers that prime mover's
monthly average output. One following generator is retained as reserve when
available. Selected generators are then combined by Unit Code. Non-CA/CT rows
are copied unchanged.

## Directly derived rates

```text
Mcf/MWh       = sum(Elec_Quantity_Mcf) / sum(Netgen_MWh)
MMBtu/MWh     = sum(Elec_MMBtu) / sum(Netgen_MWh)
fuel $/MWh    = Mcf/MWh * monthly natural-gas price ($/Mcf)
carbon t/MWh  = MMBtu/MWh * 0.05306 tCO2/MMBtu
```

`mmbtu_per_mwh` is not a separate data-processing stage. The final builder
always derives it directly from `Elec_MMBtu / Netgen` for retained non-CA/CT
rows and from the summed CA+CT quantities for combined-cycle rows. A stale
pre-existing value is overwritten. No factor of 1,000 is applied because net
generation is in MWh. Carbon is direct-combustion CO2 only; cost is fuel cost
only.

## Reproduction and validation

The default command reads the immutable ungrouped base and atomically replaces
the 36 canonical 2023--2025 files:

```powershell
python build_cc_grouped_stacks.py
python -m unittest test_build_cc_grouped_stacks.py
```

The command is idempotent: CA, CT, and any existing `CC_GROUPED` rows are
excluded from the base before the combined-cycle blocks are rebuilt. It writes
no intermediate files by default. Crosswalk and conservation workbooks can be
retained for a diagnostic run with `--provenance-dir <directory>`.

The adopted run contained 1,523 grouped plant-months, skipped 193 non-positive
plant-month records, and had a maximum extensive-quantity conservation error
of `2.328e-10`.

The stack builder does not change saved optimization Results. When gas costs or
carbon outputs must be refreshed, `../recalculate_gas_postprocess.py` applies
the rebuilt stacks to the saved hourly generation series without invoking the
optimizer/Gurobi. Only gas marginal prices, fuel costs, direct carbon
emissions, and their totals are recomputed; battery dispatch, SOC, profit, gas
generation, and renewable absorption remain unchanged.
