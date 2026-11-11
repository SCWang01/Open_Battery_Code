"""Run fixed-horizon Rolling Window members in local worker processes.

The parent validates shared inputs and writes every output; workers only solve
independent resources.  Model, random-sequence, and output contracts are the
same as ``Rolling_Window_1hour.py``. Power limits use each month's raw submitted
max(HSL)/min(LSL), with no rounding. Minimum SOC is zero; maximum SOC is the raw
monthly submitted maximum. Initial energy resets to the first observed SOC.

Running without arguments (including an editor's Run button) loads the
experiment_config_1hour.json beside this script, with 12 workers and one solver
thread per worker. Explicit CLI options can override these defaults.

Examples:
    python distributed_Rolling_Window_1hour.py
    python distributed_Rolling_Window_1hour.py --workers 4
    python distributed_Rolling_Window_1hour.py --member BIG_STAR_ESR1 --member BLSUMMIT_ESR1
    python distributed_Rolling_Window_1hour.py --steps 1 --price-samples 3 --workers 2

Every worker opens Gurobi models independently.  If the installed license
limits concurrent sessions, reduce ``--workers``.  ``--solver-threads`` limits
the threads used by each worker so process-level parallelism does not
oversubscribe the machine.
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

import Rolling_Window_1hour as rolling


DEFAULT_MAX_WORKERS = 12
DEFAULT_SOLVER_THREADS = 1
DEFAULT_EXPERIMENT_PATH = (
    Path(__file__).resolve().with_name("experiment_config_1hour.json")
)

_WORKER_CONFIG: rolling.RollingConfig | None = None
_WORKER_INNOVATIONS: np.ndarray | None = None
_WORKER_INNOVATION_ORIGIN: pd.Timestamp | None = None
_WORKER_SOLVER_THREADS: int | None = None
_WORKER_RANDOM_RECORDS: dict[str, rolling.experiment_data.RandomRecord] = {}


def _initialize_worker(
    config: rolling.RollingConfig,
    innovations: np.ndarray | None,
    innovation_origin_date: pd.Timestamp,
    solver_threads: int,
    random_records: tuple[rolling.experiment_data.RandomRecord, ...] = (),
) -> None:
    """Install immutable run data in one worker."""

    global _WORKER_CONFIG, _WORKER_INNOVATIONS
    global _WORKER_INNOVATION_ORIGIN, _WORKER_SOLVER_THREADS
    global _WORKER_RANDOM_RECORDS
    _WORKER_CONFIG = config
    _WORKER_INNOVATIONS = innovations
    _WORKER_INNOVATION_ORIGIN = innovation_origin_date
    _WORKER_SOLVER_THREADS = solver_threads
    _WORKER_RANDOM_RECORDS = {r.member: r for r in random_records}


def _run_member(resource: rolling.ResourceData) -> tuple[str, pd.DataFrame]:
    """Compute one member and return its frame to the parent process."""

    if (
        _WORKER_CONFIG is None
        or (_WORKER_INNOVATIONS is None and not _WORKER_RANDOM_RECORDS)
        or _WORKER_INNOVATION_ORIGIN is None
        or _WORKER_SOLVER_THREADS is None
    ):
        raise RuntimeError("worker was not initialized")
    innovations = _WORKER_INNOVATIONS
    if _WORKER_RANDOM_RECORDS:
        record = _WORKER_RANDOM_RECORDS[resource.name]
        innovations = np.load(record.path, allow_pickle=False)
    frame = rolling.run_resource(
        resource,
        innovations,
        _WORKER_CONFIG,
        innovation_origin_date=_WORKER_INNOVATION_ORIGIN,
        solver_threads=_WORKER_SOLVER_THREADS,
    )
    return resource.name, frame


def _validate_parallel_options(
    workers: int | None,
    solver_threads: int,
) -> tuple[int | None, int]:
    """Validate process and solver concurrency at the CLI boundary."""

    if workers is not None and (isinstance(workers, bool) or workers < 1):
        raise ValueError("workers must be at least 1")
    if isinstance(solver_threads, bool) or solver_threads < 1:
        raise ValueError("solver_threads must be at least 1")
    return workers, solver_threads


def _resolve_worker_count(requested: int | None, resource_count: int) -> int:
    """Cap workers at the number of independent member tasks."""

    if resource_count < 1:
        raise ValueError("no priced resources were selected")
    default_workers = min(DEFAULT_MAX_WORKERS, mp.cpu_count())
    return min(default_workers if requested is None else requested, resource_count)


def run_distributed(
    config: rolling.RollingConfig = rolling.RollingConfig(),
    requested_members: Sequence[str] | None = None,
    *,
    workers: int | None = None,
    solver_threads: int = DEFAULT_SOLVER_THREADS,
) -> rolling.DemoResult:
    """Run selected members in parallel and write serial-compatible outputs."""

    workers, solver_threads = _validate_parallel_options(workers, solver_threads)
    config = rolling.validate_config(config)
    started_at = datetime.now(timezone.utc)

    resources, skipped, innovation_origin_date = rolling.load_resources(
        config,
        requested_members,
        workers=workers or DEFAULT_MAX_WORKERS,
    )
    worker_count = _resolve_worker_count(workers, len(resources))
    print(
        f"Rolling power policy: {rolling.POWER_LIMIT_POLICY}; "
        f"SOC policy: {rolling.SOC_POLICY}; workers={worker_count}",
        flush=True,
    )
    random_records = ()
    if config.experiment_path is not None:
        if solver_threads != 1:
            raise ValueError("experiment requires --solver-threads 1")
        experiment = rolling.experiment_data.load_experiment(
            config.experiment_path, config.month
        )
        random_records = tuple(
            rolling.experiment_data.prepare_random(experiment, r.name)
            for r in resources
        )
        innovations = random_path = random_manifest_path = None
    run_provenance = rolling.begin_experiment_run(config, resources)
    frames_by_member: dict[str, pd.DataFrame] = {}
    context = mp.get_context("spawn")
    with context.Pool(
        processes=worker_count,
        initializer=_initialize_worker,
        initargs=(
            config,
            innovations,
            innovation_origin_date,
            solver_threads,
            random_records,
        ),
    ) as pool:
        for member, frame in pool.imap_unordered(_run_member, resources):
            frames_by_member[member] = frame

    ordered_frames = [frames_by_member[resource.name] for resource in resources]
    return rolling._export_run(
        config=config,
        resources=resources,
        skipped=skipped,
        frames=ordered_frames,
        started_at=started_at,
        random_path=random_path,
        random_manifest_path=random_manifest_path,
        innovation_origin_date=innovation_origin_date,
        random_records=random_records,
        run_provenance=run_provenance,
    )


def _build_argument_parser() -> argparse.ArgumentParser:
    """Build the serial-compatible CLI plus parallel controls."""

    parser = rolling._build_argument_parser()
    parser.description = __doc__
    # Resolve beside the script so an editor's working directory is irrelevant.
    parser.set_defaults(
        experiment=DEFAULT_EXPERIMENT_PATH,
        solver_threads=DEFAULT_SOLVER_THREADS,
    )
    parser.set_defaults(workers=DEFAULT_MAX_WORKERS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the distributed fixed-horizon rolling experiment."""

    arguments = _build_argument_parser().parse_args(argv)
    rolling.run_cli_months(arguments, run_distributed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
