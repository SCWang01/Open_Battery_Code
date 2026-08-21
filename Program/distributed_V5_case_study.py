# -*- coding: utf-8 -*-
"""
Distributed driver for V5_Case_Study.

The months from 2023.01 through 2025.12 are fully independent: each reads its own
input data, writes its own per-month CSV / .npy files, and contributes a single
row to the summary.  This driver schedules one month per task so the many
Gurobi solves for independent months can run in parallel.

Usage:
    python distributed_V5_case_study.py                # all configured months
    python distributed_V5_case_study.py --workers 6    # cap at 6 parallel months
    python distributed_V5_case_study.py --months 0 1 2  # only a subset (0-based)

Notes:
 - Each worker solves Gurobi models concurrently.  A size-limited or
   single-session Gurobi license may reject concurrent sessions; an academic
   named-user / full license normally allows many local sessions.  If you hit a
   licensing error, lower --workers to 1 to confirm, then raise as allowed.
 - Inner per-hour progress bars are silenced in workers to keep output legible;
   the parent shows a single bar counting completed months.
 - The split ratio k is read from V5_Case_Study.k, so every per-month file and
   the summary carry the same _k{int(k*100)} tag as a direct V5 run.
 - Outputs land directly in the project's Results directory under the same names
   a sequential V5 run writes, so Program/analyze_summary.py and the Figure 4
   plotting workflows under Figure_Plot/ consume them without any path edit.
"""

import argparse
import calendar
import multiprocessing as mp
from pathlib import Path

import pandas as pd
from tqdm import tqdm

import V5_Case_Study as v5

# Every per-month CSV / .npy file and the summary are written directly into the
# project's Results directory, with no dated or parameter-tagged subfolder, so a
# distributed run produces exactly the flat layout a sequential V5 run does.
# Reusing v5.RESULTS_DIR keeps a single definition of that location and keeps the
# downstream consumers that hard-code it working:
#   Results/summary_202301_202512_exact_V5_k20.csv
#       -> Program/analyze_summary.py (its default input)
#       -> Results/analysis_202301_202512.xlsx (source data for Figure 4d)
#   Results/April2025_eta95%_std2_exact_V5_k20.csv
#       -> Figure_Plot/figure_plot_4_e/input/ (source data for Figure 4e)
# Because the names are parameter-derived rather than run-derived, repeated runs
# overwrite same-parameter files in place; preserve any results you still need.
OUTPUT_DIR = v5.RESULTS_DIR


def _initialize_worker(output_dir):
    """Configure one worker once with the parent's output directory."""
    _orig_tqdm = v5.tqdm

    def _quiet(*args, **kwargs):
        kwargs['disable'] = True
        return _orig_tqdm(*args, **kwargs)

    v5.tqdm = _quiet
    v5.RESULTS_DIR = Path(output_dir)


def run_single_month(num):
    """Compute one month and return its summary and detection-count row.

    Delegates to v5.run_one_month, the single source of truth for the per-month
    pipeline, so any edit to that logic applies here automatically.  It runs in
    a worker process, where changes to v5's month globals are isolated from the
    other workers.
    """
    summary, detection_counts = v5.run_one_month(
        num, return_detection_counts=True
    )
    return num, summary, detection_counts


def main():
    parser = argparse.ArgumentParser(description='Distributed V5 case study.')
    parser.add_argument(
        '--workers', type=int, default=min(12, mp.cpu_count()),
        help='number of parallel worker processes (default: min(12, CPU count))',
    )
    parser.add_argument(
        '--months', type=int, nargs='+', default=list(range(len(v5.monthlist))),
        help='0-based month indices to run (default: all configured months)',
    )
    args = parser.parse_args()

    months = sorted(set(args.months))
    if args.workers < 1:
        parser.error('--workers must be at least 1')
    invalid_months = [
        num for num in months
        if num < 0 or num >= len(v5.year_month_list)
    ]
    if invalid_months:
        parser.error(
            '--months contains out-of-range indices '
            f'{invalid_months}; valid indices are 0..{len(v5.year_month_list) - 1}'
        )

    # Validate every requested random-data file before starting expensive
    # solver workers, so missing or corrupt inputs fail once in the parent.
    for num in months:
        year, month = v5.year_month_list[num]
        n_day = calendar.monthrange(year, month)[1]
        v5.load_monthly_price_error_data(year, month, n_day)

    workers = min(args.workers, len(months))
    v5.RESULTS_DIR = OUTPUT_DIR
    output_dir = OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f'Running {len(months)} month(s) across {workers} worker(s) at k={v5.k}...')

    results = {}
    with mp.Pool(
        processes=workers,
        initializer=_initialize_worker,
        initargs=(str(output_dir),),
    ) as pool:
        for num, summary, detection_counts in tqdm(
            pool.imap_unordered(run_single_month, months),
            total=len(months), desc='Months completed', unit='month',
        ):
            results[num] = (summary, detection_counts)

    # Preserve the original month ordering in the summary file.
    summaries = [results[num][0] for num in months]
    detection_rows = [results[num][1] for num in months]
    start_year, start_month = v5.year_month_list[months[0]]
    end_year, end_month = v5.year_month_list[months[-1]]
    start_period = f'{start_year}{start_month:02d}'
    end_period = f'{end_year}{end_month:02d}'
    summary_path = output_dir / (
        f'summary_{start_period}_{end_period}_{v5.COST_MODE}_V5_k{int(v5.k*100)}.csv'
    )
    pd.DataFrame(summaries).to_csv(summary_path, index=False)
    v5.write_detection_summary(detection_rows, summary_path)
    print(f'Wrote summary: {summary_path}')
    return summary_path


if __name__ == '__main__':
    main()
