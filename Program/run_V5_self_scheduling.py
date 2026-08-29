"""Run the V5 ESS self-scheduling experiment without changing the bidding run.

The default command runs May 2025. Pass ``--all-months`` to run the complete
configured study period. All artifacts are written below
``Results/Self-Scheduling`` by :mod:`V5_Case_Study`.
"""

import argparse

from V5_Case_Study import (
    run_all_months_self_scheduling,
    run_may_2025_self_scheduling,
)


def parse_args(argv=None):
    """Parse the independent self-scheduling entry-point arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--all-months',
        action='store_true',
        help='run every configured month instead of only May 2025',
    )
    return parser.parse_args(argv)


def main(argv=None):
    """Run the requested self-scheduling scope and return its summary path."""
    args = parse_args(argv)
    if args.all_months:
        return run_all_months_self_scheduling()
    return run_may_2025_self_scheduling()


if __name__ == '__main__':
    print(main())
