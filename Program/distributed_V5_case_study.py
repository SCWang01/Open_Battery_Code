# -*- coding: utf-8 -*-
"""
Distributed driver for V5_Case_Study.

The months from 2023.01 through 2026.04 are fully independent: each reads its own
input data, writes its own per-month CSV / .npy files, and contributes a single
row to the summary.  This driver runs one month per worker process so the ~72k
Gurobi solves per month happen in parallel instead of sequentially.

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
"""

import argparse
import multiprocessing as mp
from datetime import date
from pathlib import Path

import pandas as pd
from tqdm import tqdm

import V5_Case_Study as v5

# All per-month CSV / .npy files and the summary are written below this
# project's Results directory in a dated, k-specific distributed-run folder.
PROJECT_ROOT = Path(v5.__file__).resolve().parent.parent
BASE_OUTPUT_DIR = PROJECT_ROOT / 'Results' / 'distributed'
EXPERIMENT_DATE = date.today().isoformat()
OUTPUT_DIR = BASE_OUTPUT_DIR / (
    f"{EXPERIMENT_DATE}_k{int(round(v5.k * 100))}_eta{v5.eta * 100:.0f}"
)


def _silence_inner_progress():
    """Force the per-hour tqdm bars inside V5_Case_Study to be disabled."""
    _orig_tqdm = v5.tqdm

    def _quiet(*args, **kwargs):
        kwargs['disable'] = True
        return _orig_tqdm(*args, **kwargs)

    v5.tqdm = _quiet


def run_single_month(num):
    """Compute one month and return its summary and detection-count row.

    Delegates to v5.run_one_month, the single source of truth for the per-month
    pipeline, so any edit to that logic applies here automatically.  Runs in its
    own process; setting v5's globals inside run_one_month is isolated from other
    workers.  Only the output directory is overridden before the call.
    """
    _silence_inner_progress()
    v5.RESULTS_DIR = OUTPUT_DIR

    summary, detection_counts = v5.run_one_month(
        num, return_detection_counts=True
    )
    return num, summary, detection_counts


def main():
    parser = argparse.ArgumentParser(description='Distributed V5 case study.')
    parser.add_argument(
        '--workers', type=int, default=min(16, mp.cpu_count()),
        help='number of parallel worker processes (default: min(16, CPU count))',
    )
    parser.add_argument(
        '--months', type=int, nargs='+', default=list(range(len(v5.monthlist))),
        help='0-based month indices to run (default: all configured months)',
    )
    args = parser.parse_args()

    months = sorted(set(args.months))
    workers = max(1, min(args.workers, len(months)))
    v5.RESULTS_DIR = OUTPUT_DIR
    output_dir = OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f'Running {len(months)} month(s) across {workers} worker(s) at k={v5.k}...')

    results = {}
    with mp.Pool(processes=workers) as pool:
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
