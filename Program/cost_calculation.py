from pathlib import Path

import numpy as np
import pandas as pd

_ng_cost_cache = {}
_ng_carbon_cache = {}
_NG_COST_DIR = Path(__file__).resolve().parent.parent / 'data' / 'ng_cost'

# EPA 2025 GHG Emission Factors Hub: natural-gas stationary combustion.
_CARBON_FACTOR_MTCO2_PER_MMBTU = 0.05306

_quad_coe_cache = None
_FUEL_COE_PATH = Path(__file__).resolve().parent / 'Fuel_Coe.xlsx'

def ng_cost(gas_generation, year, month):
    """Monthly convex piecewise-linear CAISO natural-gas generation cost.

    Builds a merit-order supply stack from the plant-level workbook
    (each row = one plant's capacity and marginal $/MWh, ranked cheapest
    first) and evaluates the cumulative generation cost C_gas(P) for every
    element of ``gas_generation`` in one vectorized pass.

    Parameters
    ----------
    gas_generation : array-like
        Natural-gas generation (MWh) at each time step.
    year, month : int
        Select the monthly file CAISO_NG_Final_{year}_{month:02d}.xlsx.

    Returns
    -------
    numpy.ndarray
        Generation cost ($) per element. ``P <= 0`` returns 0. Generation
        above total stack capacity is extrapolated at the most expensive
        (last) plant's marginal price.
    """
    key = (int(year), int(month))
    if key not in _ng_cost_cache:
        path = _NG_COST_DIR / f'CAISO_NG_Final_{key[0]}_{key[1]:02d}.xlsx'
        df = pd.read_excel(path)

        # Each row contains one plant's available capacity.  The workbook is
        # already ranked by marginal cost; sorting again makes that assumption
        # explicit while preserving the order of equal-price rows.
        supply = df[['capacity', '$_per_mwh']].copy()
        supply['capacity'] = pd.to_numeric(supply['capacity'], errors='coerce')
        supply['$_per_mwh'] = pd.to_numeric(supply['$_per_mwh'], errors='coerce')
        supply = supply.replace([np.inf, -np.inf], np.nan).dropna()
        supply = supply[supply['capacity'] > 0.0]
        supply = supply.sort_values('$_per_mwh', kind='stable')
        if supply.empty:
            raise ValueError(f'No valid natural-gas supply data found in {path}')

        unit_cap = supply['capacity'].to_numpy(dtype=float)
        price = supply['$_per_mwh'].to_numpy(dtype=float)
        cap = np.cumsum(unit_cap)
        lower = np.concatenate([[0.0], cap[:-1]])
        cum = np.concatenate([[0.0], np.cumsum(unit_cap * price)[:-1]])
        _ng_cost_cache[key] = (cap, price, lower, cum)

    cap, price, lower, cum = _ng_cost_cache[key]
    gas = np.asarray(gas_generation, dtype=float)
    idx = np.clip(np.searchsorted(cap, gas, side='left'), 0, len(cap) - 1)
    return np.where(gas <= 0.0, 0.0, cum[idx] + (gas - lower[idx]) * price[idx])


def ng_carbon_emission(gas_generation, year, month):
    """Monthly piecewise-linear CAISO natural-gas carbon emissions.

    Uses the same cost-ranked merit-order stack as :func:`ng_cost`.  Once each
    plant's dispatched generation has been determined by that stack, its
    emissions are calculated as::

        generation (MWh)
        * mmbtu_per_mwh (MMBtu/MWh)
        * 0.05306 (metric tonnes CO2/MMBtu)

    Parameters
    ----------
    gas_generation : array-like
        Natural-gas generation (MWh) at each time step.
    year, month : int
        Select the monthly file CAISO_NG_Final_{year}_{month:02d}.xlsx.

    Returns
    -------
    numpy.ndarray
        Carbon emissions (metric tonnes CO2) per element. ``P <= 0`` returns
        0. Generation above total stack capacity is extrapolated using the
        last (most expensive) plant's emissions intensity.
    """
    key = (int(year), int(month))
    if key not in _ng_carbon_cache:
        path = _NG_COST_DIR / f'CAISO_NG_Final_{key[0]}_{key[1]:02d}.xlsx'
        df = pd.read_excel(path)

        # Dispatch order is determined only by marginal generation cost.  The
        # carbon columns are carried along after sorting and do not influence
        # which plants are called.
        columns = ['capacity', '$_per_mwh', 'mmbtu_per_mwh']
        supply = df[columns].copy()
        for column in columns:
            supply[column] = pd.to_numeric(supply[column], errors='coerce')

        # Mirror ng_cost's stack construction exactly: cost data determine
        # membership and order; carbon data are validated only after that
        # dispatch stack has been formed.
        supply[['capacity', '$_per_mwh']] = supply[
            ['capacity', '$_per_mwh']
        ].replace([np.inf, -np.inf], np.nan)
        supply = supply.dropna(subset=['capacity', '$_per_mwh'])
        supply = supply[supply['capacity'] > 0.0]
        supply = supply.sort_values('$_per_mwh', kind='stable')
        if supply.empty:
            raise ValueError(f'No valid natural-gas supply data found in {path}')

        carbon_parameters = supply[['mmbtu_per_mwh']]
        invalid_carbon = (
            ~np.isfinite(carbon_parameters).all(axis=1)
            | (carbon_parameters <= 0.0).any(axis=1)
        )
        if invalid_carbon.any():
            invalid_rows = ', '.join(
                str(int(row_index) + 2)
                for row_index in supply.index[invalid_carbon]
            )
            raise ValueError(
                f'Invalid mmbtu_per_mwh in {path} '
                f'(Excel row(s): {invalid_rows})'
            )

        unit_cap = supply['capacity'].to_numpy(dtype=float)
        carbon_intensity = (
            supply['mmbtu_per_mwh'].to_numpy(dtype=float)
            * _CARBON_FACTOR_MTCO2_PER_MMBTU
        )
        cap = np.cumsum(unit_cap)
        lower = np.concatenate([[0.0], cap[:-1]])
        cum = np.concatenate([
            [0.0], np.cumsum(unit_cap * carbon_intensity)[:-1]
        ])
        _ng_carbon_cache[key] = (cap, carbon_intensity, lower, cum)

    cap, carbon_intensity, lower, cum = _ng_carbon_cache[key]
    gas = np.asarray(gas_generation, dtype=float)
    idx = np.clip(np.searchsorted(cap, gas, side='left'), 0, len(cap) - 1)
    return np.where(
        gas <= 0.0,
        0.0,
        cum[idx] + (gas - lower[idx]) * carbon_intensity[idx],
    )


def _load_quad_coefficients():
    """Read every monthly quadratic fit from Fuel_Coe.xlsx exactly once.

    Returns a dict keyed by ``(year, month)`` mapping to the ``(a, b, c)``
    coefficients of ``cost = a * gas**2 + b * gas + c``.  The workbook is
    produced by the external Qua_Fit.py script (not included in this
    repository) and holds one row per month.
    """
    global _quad_coe_cache
    if _quad_coe_cache is None:
        df = pd.read_excel(_FUEL_COE_PATH)
        cache = {}
        for row in df.itertuples(index=False):
            cache[(int(row.year), int(row.month))] = (
                float(row.a), float(row.b), float(row.c)
            )
        if not cache:
            raise ValueError(f'No quadratic coefficients found in {_FUEL_COE_PATH}')
        _quad_coe_cache = cache
    return _quad_coe_cache


def quad_cost(gas_generation, year, month):
    """Monthly quadratic natural-gas generation cost from Fuel_Coe.xlsx.

    Evaluates ``cost = a * gas**2 + b * gas + c`` using the per-month
    coefficients fitted by the external Qua_Fit.py script (not included in
    this repository).  Mirrors :func:`ng_cost` at the zero
    boundary: ``gas <= 0`` returns 0.  Values above the fitted ``gas_max`` are
    extrapolated by the quadratic itself (convex, since ``a > 0``).

    Parameters
    ----------
    gas_generation : array-like
        Natural-gas generation (MWh) at each time step.
    year, month : int
        Select the monthly coefficient row for ``(year, month)``.

    Returns
    -------
    numpy.ndarray
        Generation cost ($) per element.
    """
    key = (int(year), int(month))
    coefficients = _load_quad_coefficients()
    if key not in coefficients:
        raise KeyError(
            f'No quadratic coefficients for {key[0]}-{key[1]:02d} in {_FUEL_COE_PATH}'
        )
    a, b, c = coefficients[key]
    gas = np.asarray(gas_generation, dtype=float)
    return np.where(gas <= 0.0, 0.0, a * gas**2 + b * gas + c)


def ng_marginal_price(gas_generation, year, month):
    """Marginal ($/MWh) natural-gas price under the merit-order stack.

    The marginal price at generation ``P`` is the ``$_per_mwh`` of the plant
    serving the last incremental MWh, i.e. ``price[idx]`` for the same merit
    -order bucket :func:`ng_cost` charges against.  ``P <= 0`` returns 0, and
    generation above total stack capacity is extrapolated at the most
    expensive (last) plant's marginal price.
    """
    # ng_cost populates and validates _ng_cost_cache[key]; call it (result
    # unused) so we reuse the exact same cached, sorted supply stack.
    ng_cost(0.0, year, month)
    cap, price, lower, cum = _ng_cost_cache[(int(year), int(month))]
    gas = np.asarray(gas_generation, dtype=float)
    idx = np.clip(np.searchsorted(cap, gas, side='left'), 0, len(cap) - 1)
    return np.where(gas <= 0.0, 0.0, price[idx])


def quad_marginal_price(gas_generation, year, month):
    """Marginal ($/MWh) natural-gas price from the quadratic fit.

    Derivative of ``cost = a*gas**2 + b*gas + c`` w.r.t. ``gas``, i.e.
    ``2*a*gas + b``.  Mirrors :func:`quad_cost` at the zero boundary:
    ``gas <= 0`` returns 0.
    """
    key = (int(year), int(month))
    coefficients = _load_quad_coefficients()
    if key not in coefficients:
        raise KeyError(
            f'No quadratic coefficients for {key[0]}-{key[1]:02d} in {_FUEL_COE_PATH}'
        )
    a, b, _c = coefficients[key]
    gas = np.asarray(gas_generation, dtype=float)
    return np.where(gas <= 0.0, 0.0, 2.0 * a * gas + b)


def gas_cost(gas_generation, year, month, mode='exact'):
    """Dispatch natural-gas cost evaluation between the two supported modes.

    ``'exact'``     -> piecewise-linear merit-order cost (:func:`ng_cost`).
    ``'quadratic'`` -> fitted quadratic cost (:func:`quad_cost`).
    """
    if mode == 'exact':
        return ng_cost(gas_generation, year, month)
    if mode == 'quadratic':
        return quad_cost(gas_generation, year, month)
    raise ValueError(f"Unknown cost mode {mode!r}; expected 'exact' or 'quadratic'")


def gas_marginal_price(gas_generation, year, month, mode='exact'):
    """Dispatch marginal natural-gas price between the two supported modes.

    ``'exact'``     -> merit-order marginal price (:func:`ng_marginal_price`).
    ``'quadratic'`` -> quadratic-fit derivative (:func:`quad_marginal_price`).
    """
    if mode == 'exact':
        return ng_marginal_price(gas_generation, year, month)
    if mode == 'quadratic':
        return quad_marginal_price(gas_generation, year, month)
    raise ValueError(f"Unknown cost mode {mode!r}; expected 'exact' or 'quadratic'")
