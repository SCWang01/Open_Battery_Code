"""Run distributed Micro optimization, verification, analysis and all Micro plots.

Use ``python -u -B Program/Micro/run_pipeline.py --workers 4``. Existing active
outputs are replaced. Each run has separate stage logs and status.json; failure
stops dependent stages. The release-root run_all.py extends this same workflow
with the thermal Macro calculation and figures.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from project_paths import DEFAULT_EXPERIMENT_PATH, MICRO_RESULTS_DIR, PROJECT_ROOT
from Experiment_Data_1hour import experiment_months


@dataclass(frozen=True)
class Stage:
    """One script and its CLI arguments, executed in the release directory."""

    name: str
    script: Path
    arguments: tuple[str, ...] = ()

    def command(self) -> list[str]:
        return [sys.executable, "-u", "-B", str(self.script), *self.arguments]


def build_micro_stages(workers: int, *, postprocess_only: bool = False) -> list[Stage]:
    """Order all configured months' stages; verify before consuming dispatch."""
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError("workers must be a positive integer")
    program = PROJECT_ROOT / "Program/Micro"
    figures = PROJECT_ROOT / "Figs/Micro"
    stages = []
    if not postprocess_only:
        stages.extend(
            [
                Stage(
                    "preflight",
                    program / "Rolling_Window_1hour.py",
                    ("--validate-only", "--workers", str(workers)),
                ),
                Stage(
                    "rolling",
                    program / "distributed_Rolling_Window_1hour.py",
                    ("--workers", str(workers), "--solver-threads", "1"),
                ),
            ]
        )
    stages.extend(
        [
            Stage(
                "verify_rolling",
                program / "Experiment_Data_1hour.py",
                (
                    "--experiment",
                    str(DEFAULT_EXPERIMENT_PATH),
                    "--verify-results",
                    "rolling",
                ),
            ),
            Stage("realized", program / "Reality_Profit.py"),
            Stage("classify", program / "classify_p_ess.py"),
            Stage("match", program / "Realized_Match.py"),
            Stage("compare", program / "Compare_Month.py"),
            Stage("classification_figures", figures / "plot_classification_all.py"),
            Stage("profit_figures", figures / "Profit_Increment.py"),
            Stage(
                "example_figure",
                figures / "Classification_Test/plot_classification_example.py",
            ),
        ]
    )
    for month in experiment_months(DEFAULT_EXPERIMENT_PATH):
        token = month.replace("-", "")
        stages.append(
            Stage(f"abc_{token}", figures / "plot_esr_abc.py", ("--month", token))
        )
    return stages


def run_stages(stages: list[Stage], *, log_root: Path, dry_run: bool = False) -> int:
    """Validate all entries, then execute with per-stage logs and atomic status.

    Dry runs only print commands and never create logs or replace outputs.
    Return zero on success, the failed child's exit code on failure, or 130 on
    interruption. Completed earlier stages remain available for inspection.
    """
    for stage in stages:
        if not stage.script.is_file():
            raise FileNotFoundError(f"missing stage script: {stage.script}")
    if dry_run:
        for index, stage in enumerate(stages, start=1):
            print(
                f"{index:02d}_{stage.name}: {subprocess.list2cmdline(stage.command())}"
            )
        return 0

    run_id = datetime.now().strftime("%Y%m%dT%H%M%S_%f")
    log_dir = log_root / run_id
    log_dir.mkdir(parents=True, exist_ok=False)
    # Each resource uses one solver thread; also cap numeric-library threads.
    environment = {
        **os.environ,
        "GIT_OPTIONAL_LOCKS": "0",
        "MPLBACKEND": "Agg",
        "PYTHONUTF8": "1",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
    }
    status = {"run_id": run_id, "status": "running", "stages": []}

    def save_status() -> None:
        temporary = log_dir / "status.tmp"
        temporary.write_text(json.dumps(status, indent=2), encoding="utf-8")
        temporary.replace(log_dir / "status.json")

    print(f"Logs: {log_dir}", flush=True)
    for index, stage in enumerate(stages, start=1):
        name = f"{index:02d}_{stage.name}"
        log_path = log_dir / f"{name}.log"
        command = stage.command()
        record = {
            "name": name,
            "command": command,
            "status": "running",
            "started": datetime.now().astimezone().isoformat(),
            "log": str(log_path),
        }
        status["stages"].append(record)
        save_status()
        started = time.monotonic()
        print(
            f"[{index}/{len(stages)}] Starting {stage.name}; log: {log_path}",
            flush=True,
        )
        try:
            with log_path.open("w", encoding="utf-8") as log:
                completed = subprocess.run(
                    command,
                    cwd=PROJECT_ROOT,
                    env=environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
            code = completed.returncode
            record.update(returncode=code, status="complete" if code == 0 else "failed")
        except KeyboardInterrupt:
            record.update(
                returncode=130, status="cancelled", error="Interrupted by user"
            )
        except OSError as exc:
            record.update(returncode=1, status="failed", error=str(exc))
        record["elapsed_seconds"] = round(time.monotonic() - started, 3)
        record["finished"] = datetime.now().astimezone().isoformat()
        if record["status"] != "complete":
            status["status"] = record["status"]
            save_status()
            print(
                f"{record['status'].upper()}: {stage.name}; see {log_path}", flush=True
            )
            return record["returncode"]
        save_status()
        print(f"Completed {stage.name} in {record['elapsed_seconds']} s", flush=True)
    status["status"] = "complete"
    save_status()
    print(f"Pipeline complete; status: {log_dir / 'status.json'}", flush=True)
    return 0


def create_parser(description: str) -> argparse.ArgumentParser:
    """Shared controls for the Micro-only and complete thermal workflows."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--workers",
        type=int,
        default=12,
        help="parallel resource processes (default: 12)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print all commands without running or writing files",
    )
    parser.add_argument(
        "--postprocess-only",
        action="store_true",
        help="reuse complete Rolling results under the current SOC policy; still verify them first",
    )
    return parser


def main() -> int:
    parser = create_parser(__doc__)
    arguments = parser.parse_args()
    try:
        stages = build_micro_stages(
            arguments.workers, postprocess_only=arguments.postprocess_only
        )
        return run_stages(
            stages,
            log_root=MICRO_RESULTS_DIR / "reports/pipeline_runs",
            dry_run=arguments.dry_run,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
