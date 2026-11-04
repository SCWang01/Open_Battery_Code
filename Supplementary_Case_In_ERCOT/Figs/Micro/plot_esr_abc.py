"""Render pooled a+b and mean-ESR c PNGs for a calendar month.

Run from any directory. Defaults to May 2026 and all configured members.
Both figures use the complete configured ESR population; c prices are the
independent hourly Hub averages. Existing classified results are read directly.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

from esr_figure_data import (
    configured_resources,
    create_argument_parser,
    load_month_data,
    load_hub_price_data,
)
from figure_plot_4_a_b.plot_state_distribution_pies import (
    aggregate_distributions,
    plot_aggregate,
)
from figure_plot_4_c.plot_hourly_state_counts_price import (
    aggregate_figure_data,
    plot_aggregate as plot_c,
)


def main() -> None:
    parser = create_argument_parser(__doc__, allow_resource=False)
    arguments = parser.parse_args()
    # Validate the full batch before exporting, so malformed later members do
    # not leave a partially generated collection.
    try:
        members = [
            load_month_data(resource, arguments.month)
            for resource in configured_resources()
        ]
        aggregate = aggregate_distributions(members)
        c_data = aggregate_figure_data(members, load_hub_price_data(arguments.month))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    aggregate_path = plot_aggregate(aggregate)
    c_path = plot_c(c_data)
    print(
        f"ALL_ESR {arguments.month}: {len(members)} resources; "
        f"{aggregate.valid_rows}/{aggregate.raw_rows} state hours; "
        f"excluded zero-limit hours={aggregate.excluded_zero_limit}"
    )
    print(f"  {aggregate_path}")
    print(f"  {aggregate_path.with_name(f'{aggregate_path.stem}_source_data.csv')}")
    print(f"  {c_path}")
    print(f"  {c_path.with_name(f'{c_path.stem}_source_data.csv')}")
    print(
        "Hub daily samples/hour="
        + ",".join(str(price.sample_size) for price in c_data.hub_prices.prices)
    )
    print("Exported one pooled a+b and one mean-ESR c PNG at 720 dpi, each with source CSV.")


if __name__ == "__main__":
    main()
