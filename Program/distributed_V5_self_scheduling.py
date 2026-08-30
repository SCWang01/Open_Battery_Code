"""Distributed driver for the V5 self-scheduling experiment.

The configured study months are independent. Each worker runs one month,
writes its hourly CSV to ``Results/Self-Scheduling``, and returns one summary
row to the parent process.

Usage::

    python distributed_V5_self_scheduling.py
    python distributed_V5_self_scheduling.py --workers 6
    python distributed_V5_self_scheduling.py --months 0 1 2
"""

import argparse
import calendar
import math
import multiprocessing as mp
from pathlib import Path

import pandas as pd
from tqdm import tqdm

import V5_Case_Study as v5


OUTPUT_DIR = v5.SELF_SCHEDULING_RESULTS_DIR


def _initialize_worker(output_dir, meanstd):
    """Configure one worker with explicit experiment parameters and quiet tqdm."""
    original_tqdm = v5.tqdm

    def quiet_tqdm(*args, **kwargs):
        kwargs['disable'] = True
        return original_tqdm(*args, **kwargs)

    v5.tqdm = quiet_tqdm
    v5.SELF_SCHEDULING_RESULTS_DIR = Path(output_dir)
    v5.meanstd = meanstd


def run_single_month(num):
    """Run one self-scheduling month and return its index and summary row."""
    return num, v5.run_one_month_self_scheduling(num)


def parse_args(argv=None):
    """Parse the distributed runner options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--workers', type=int, default=min(12, mp.cpu_count()),
        help='number of parallel worker processes (default: min(12, CPU count))',
    )
    parser.add_argument(
        '--months', type=int, nargs='+',
        default=list(range(len(v5.monthlist))),
        help='0-based month indices to run (default: all configured months)',
    )
    parser.add_argument(
        '--meanstd', type=float, default=v5.meanstd,
        help=f'price prediction error percentage (default: {v5.meanstd:g})',
    )
    parser.add_argument(
        '--output-dir', type=Path, default=OUTPUT_DIR,
        help=f'output directory (default: {OUTPUT_DIR})',
    )
    return parser.parse_args(argv)


def validate_months(months, workers):
    """Validate and normalize requested month indices before worker startup."""
    if workers < 1:
        raise ValueError('--workers must be at least 1')
    normalized = sorted(set(months))
    invalid = [
        num for num in normalized
        if num < 0 or num >= len(v5.year_month_list)
    ]
    if invalid:
        raise ValueError(
            '--months contains out-of-range indices '
            f'{invalid}; valid indices are 0..{len(v5.year_month_list) - 1}'
        )
    if not normalized:
        raise ValueError('--months must contain at least one month')
    return normalized


def validate_meanstd(value):
    """Return a finite non-negative price-error percentage."""
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError('--meanstd must be a finite non-negative number')
    return value


def run_months(months, workers, meanstd=None, output_dir=None):
    """Run selected months and write an ordered aggregate summary."""
    months = validate_months(months, workers)
    meanstd = validate_meanstd(v5.meanstd if meanstd is None else meanstd)
    output_dir = Path(OUTPUT_DIR if output_dir is None else output_dir)
    v5.meanstd = meanstd
    v5.SELF_SCHEDULING_RESULTS_DIR = output_dir
    for num in months:
        year, month = v5.year_month_list[num]
        n_day = calendar.monthrange(year, month)[1]
        v5.load_monthly_price_error_data(year, month, n_day)

    output_dir.mkdir(parents=True, exist_ok=True)
    worker_count = min(workers, len(months))
    print(
        f'Running {len(months)} self-scheduling month(s) across '
        f'{worker_count} worker(s) at k={v5.k}...'
    )

    results = {}
    with mp.Pool(
        processes=worker_count,
        initializer=_initialize_worker,
        initargs=(str(output_dir), meanstd),
    ) as pool:
        for num, summary in tqdm(
            pool.imap_unordered(run_single_month, months),
            total=len(months), desc='Self-scheduling months completed',
            unit='month',
        ):
            summary = dict(summary)
            summary['meanstd'] = meanstd
            results[num] = summary

    summaries = [results[num] for num in months]
    start_year, start_month = v5.year_month_list[months[0]]
    end_year, end_month = v5.year_month_list[months[-1]]
    start_period = f'{start_year}{start_month:02d}'
    end_period = f'{end_year}{end_month:02d}'
    summary_path = output_dir / (
        f'summary_{start_period}_{end_period}_{v5.COST_MODE}_'
        f'V5_self_scheduling_k{int(v5.k * 100)}.csv'
    )
    pd.DataFrame(summaries).to_csv(summary_path, index=False)
    print(f'Wrote summary: {summary_path}')
    return summary_path


def main(argv=None):
    """Run the selected self-scheduling months."""
    args = parse_args(argv)
    try:
        return run_months(
            args.months,
            args.workers,
            meanstd=args.meanstd,
            output_dir=args.output_dir,
        )
    except ValueError as exc:
        raise SystemExit(f'error: {exc}') from exc


if __name__ == '__main__':
    print(main())
