"""Compatibility CLI for the all-ESR mean-count Figure C workflow.

State counts are summed across all configured ESRs and divided by their fixed
resource count. The price curve and daily Student-t mean interval come from
the independent monthly HB_HUBAVG hourly source. This entry point accepts
--month YYYYMM (default 202605), without a single-resource selector, and writes
one transparent PNG with a source-data CSV. No intermediate XLSX is used.
"""

from plot_hourly_state_counts_price import main


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Error: {exc}") from None
