"""Exact hourly ERCOT gas, coal or combined thermal fuel cost and CO2.

The evaluator uses cost-ranked blocks from monthly ERCOT supply workbooks.
It is a fuel-cost model, not an ERCOT market-settlement or commitment model.
Power is hourly mean MW; outputs are dollars and tonnes CO2 for one hour.
At a capacity breakpoint the lower block sets the marginal price. Beyond
total capacity, the final block's cost and emissions intensity extrapolate.
"""

from dataclasses import dataclass
import re
from typing import Any

import numpy as np


# EPA stationary-combustion CO2 factors, converted from kg to tonnes/MMBtu.
# https://www.epa.gov/system/files/documents/2025-01/ghg-emission-factors-hub-2025.pdf
CARBON_FACTORS_T_PER_MMBTU = {"NG": 0.05306, "SUB": 0.09717, "LIG": 0.09772}
CARBON_FACTOR_T_PER_MMBTU = CARBON_FACTORS_T_PER_MMBTU["NG"]
FUELS_BY_BASIS = {
    "gas": ("NG",),
    "coal": ("SUB", "LIG"),
    "thermal": ("NG", "SUB", "LIG"),
}
STACK_COLUMNS = (
    "YEAR",
    "MONTH",
    "capacity",
    "$_per_mwh",
    "mmbtu_per_mwh",
    "carbon_t_per_mwh",
)


@dataclass(frozen=True)
class CostEvaluation:
    """One-hour selected-fuel metrics, matching the input power shape.

    ``cost`` is fuel cost in dollars, ``marginal_price`` is dollars/MWh,
    ``carbon`` is tonnes CO2, and ``excess_mw`` is power above stack capacity.
    """

    cost: np.ndarray
    marginal_price: np.ndarray
    carbon: np.ndarray
    excess_mw: np.ndarray


def _numeric_array(values: Any, name: str) -> np.ndarray:
    try:
        raw = np.asarray(values, dtype=object)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must contain numeric values") from exc
    if any(isinstance(value, (bool, np.bool_)) for value in raw.flat):
        raise ValueError(f"{name} must contain numeric values, not booleans")
    try:
        array = np.asarray(raw, dtype=float)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must contain numeric values") from exc
    invalid = ~np.isfinite(array)
    if invalid.any():
        indices = np.flatnonzero(invalid).tolist()
        raise ValueError(f"{name} contains non-finite values at flat indices {indices}")
    return array


def _readonly(values: np.ndarray) -> np.ndarray:
    result = np.asarray(values, dtype=float).copy()
    result.setflags(write=False)
    return result


@dataclass(frozen=True)
class CostStack:
    """Validated monthly merit-order stack; construct with ``from_frame``.

    The frame must supply all ``STACK_COLUMNS`` and contain only the requested
    YYYYMM. Every row is validated; invalid blocks are never silently dropped.
    Cost ties retain workbook row order, including their carbon intensities.
    """

    month: str
    _capacity_limits: np.ndarray
    _lower_limits: np.ndarray
    _prices: np.ndarray
    _cost_before: np.ndarray
    _carbon_intensities: np.ndarray
    _carbon_before: np.ndarray
    basis: str = "gas"

    @property
    def capacity_mw(self) -> float:
        """Total capacity of the validated supply stack in MW."""
        return float(self._capacity_limits[-1])

    @classmethod
    def from_frame(cls, frame: Any, month: str, *, basis: str = "gas") -> "CostStack":
        """Validate workbook rows and assemble a stable cost-ranked stack.

        ``frame`` may be a pandas DataFrame or a mapping of column arrays.
        Capacity and heat rate must be positive, fuel prices nonnegative.
        Carbon intensity must match heat rate times the primary_fuel factor.
        Only gas accepts a missing primary_fuel column (legacy NG inputs).
        """
        if not isinstance(basis, str) or basis not in FUELS_BY_BASIS:
            raise ValueError(f"basis must be gas, coal or thermal; received {basis!r}")
        if (
            not isinstance(month, str)
            or re.fullmatch(r"\d{4}(0[1-9]|1[0-2])", month) is None
        ):
            raise ValueError(f"month must be YYYYMM; received {month!r}")
        year, month_number = int(month[:4]), int(month[4:])
        if year < 1:
            raise ValueError(f"month must have a positive year; received {month!r}")
        try:
            missing = [column for column in STACK_COLUMNS if column not in frame]
        except TypeError as exc:
            raise ValueError("stack frame must supply named columns") from exc
        if missing:
            raise ValueError(f"stack frame is missing columns: {', '.join(missing)}")

        columns = {name: _numeric_array(frame[name], name) for name in STACK_COLUMNS}
        if any(values.ndim != 1 for values in columns.values()):
            raise ValueError("stack columns must be one-dimensional")
        sizes = {len(values) for values in columns.values()}
        if len(sizes) != 1:
            raise ValueError("stack columns must have the same number of rows")
        if not sizes or sizes == {0}:
            raise ValueError("stack frame contains no supply blocks")
        identity_matches = (columns["YEAR"] == year) & (
            columns["MONTH"] == month_number
        )
        if not identity_matches.all():
            rows = np.flatnonzero(~identity_matches).tolist()
            raise ValueError(
                f"stack YEAR/MONTH must match {month}; invalid rows {rows}"
            )

        for name in ("capacity", "mmbtu_per_mwh", "carbon_t_per_mwh"):
            invalid = columns[name] <= 0.0
            if invalid.any():
                rows = np.flatnonzero(invalid).tolist()
                raise ValueError(f"{name} must be positive; invalid rows {rows}")
        negative_prices = columns["$_per_mwh"] < 0.0
        if negative_prices.any():
            rows = np.flatnonzero(negative_prices).tolist()
            raise ValueError(f"$_per_mwh must be nonnegative; invalid rows {rows}")

        if "primary_fuel" not in frame:
            if basis != "gas":
                raise ValueError(f"{month} {basis}: missing primary_fuel column")
            fuels = np.full(len(columns["capacity"]), "NG", dtype=object)
        else:
            fuels = np.asarray(frame["primary_fuel"], dtype=object)
            if fuels.ndim != 1 or len(fuels) != len(columns["capacity"]):
                raise ValueError(
                    "primary_fuel must be one-dimensional with the same number of rows"
                )
        invalid_fuels = [
            (index, fuel)
            for index, fuel in enumerate(fuels)
            if not isinstance(fuel, str) or fuel not in FUELS_BY_BASIS[basis]
        ]
        if invalid_fuels:
            raise ValueError(
                f"{month} {basis}: primary_fuel must be one of {FUELS_BY_BASIS[basis]}; "
                f"invalid rows/fuels {invalid_fuels}"
            )
        factors = np.array([CARBON_FACTORS_T_PER_MMBTU[fuel] for fuel in fuels])
        carbon_intensity = columns["mmbtu_per_mwh"] * factors
        carbon_matches = np.isclose(
            columns["carbon_t_per_mwh"], carbon_intensity, rtol=1e-9, atol=1e-12
        )
        if not carbon_matches.all():
            rows = np.flatnonzero(~carbon_matches).tolist()
            raise ValueError(
                f"{month} {basis}: carbon_t_per_mwh must match mmbtu_per_mwh * "
                f"primary_fuel factor; invalid rows {rows}"
            )

        order = np.argsort(columns["$_per_mwh"], kind="stable")
        capacity = columns["capacity"][order]
        prices = columns["$_per_mwh"][order]
        intensity = carbon_intensity[order]
        with np.errstate(over="ignore", invalid="ignore"):
            limits = np.cumsum(capacity)
            lower = np.concatenate(([0.0], limits[:-1]))
            cost_before = np.concatenate(([0.0], np.cumsum(capacity * prices)[:-1]))
            carbon_before = np.concatenate(
                ([0.0], np.cumsum(capacity * intensity)[:-1])
            )
        if not all(
            np.isfinite(values).all()
            for values in (limits, lower, cost_before, carbon_before)
        ):
            raise ValueError("stack cumulative capacity, cost, or carbon overflowed")

        return cls(
            month=month,
            _capacity_limits=_readonly(limits),
            _lower_limits=_readonly(lower),
            _prices=_readonly(prices),
            _cost_before=_readonly(cost_before),
            _carbon_intensities=_readonly(intensity),
            _carbon_before=_readonly(carbon_before),
            basis=basis,
        )

    def evaluate(self, power: Any) -> CostEvaluation:
        """Evaluate finite hourly mean MW, retaining its scalar/array shape.

        Nonpositive power yields zero for all metrics. Positive power above
        the stack extrapolates using the last block, matching legacy exact mode.
        """
        generation = _numeric_array(power, "power")
        index = np.clip(
            np.searchsorted(self._capacity_limits, generation, side="left"),
            0,
            len(self._capacity_limits) - 1,
        )
        positive = generation > 0.0
        active_power = np.maximum(generation, 0.0)
        increment = active_power - self._lower_limits[index]
        with np.errstate(over="ignore", invalid="ignore"):
            cost = np.where(
                positive,
                self._cost_before[index] + increment * self._prices[index],
                0.0,
            )
            carbon = np.where(
                positive,
                self._carbon_before[index]
                + increment * self._carbon_intensities[index],
                0.0,
            )
        if not np.isfinite(cost).all() or not np.isfinite(carbon).all():
            raise ValueError("power produces non-finite cost or carbon")
        return CostEvaluation(
            cost=np.asarray(cost),
            marginal_price=np.asarray(np.where(positive, self._prices[index], 0.0)),
            carbon=np.asarray(carbon),
            excess_mw=np.asarray(np.maximum(active_power - self.capacity_mw, 0.0)),
        )


# Preserve the original result import and gas-only constructor contract.
GasEvaluation = CostEvaluation


class GasCostStack(CostStack):
    """Compatibility entry point for natural-gas workbooks only."""

    @classmethod
    def from_frame(cls, frame: Any, month: str) -> "GasCostStack":
        return super().from_frame(frame, month, basis="gas")
