"""Run the Micro workflow with distributed Rolling; stop on the first failure.

Use ``python -u -B Program/Micro/run_pipeline.py`` from the project root.
This recomputes active results and figures; historical archives are untouched.
Every invocation has separate logs and a machine-readable status file.
"""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from project_paths import MICRO_RESULTS_DIR, PROJECT_ROOT


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=12, help="parallel resource processes (default: 12)")
    arguments = parser.parse_args()
    if arguments.workers < 1:
        parser.error("--workers must be at least 1")
    run_id = datetime.now().strftime("%Y%m%dT%H%M%S")
    log_dir = MICRO_RESULTS_DIR / "reports" / "pipeline_runs" / run_id
    log_dir.mkdir(parents=True, exist_ok=False)
    program = PROJECT_ROOT / "Program/Micro"
    figures = PROJECT_ROOT / "Figs/Micro"
    stages = [
        ("01_preflight", program / "Rolling_Window_1hour.py", ["--validate-only", "--workers", str(arguments.workers)]),
        ("02_rolling", program / "distributed_Rolling_Window_1hour.py", ["--workers", str(arguments.workers), "--solver-threads", "1"]),
        ("03_realized", program / "Reality_Profit.py", []),
        ("04_classify", program / "classify_p_ess.py", []),
        ("05_match", program / "Realized_Match.py", []),
        ("06_compare", program / "Compare_Month.py", []),
        ("07_classification_figures", figures / "plot_classification_all.py", []),
        ("08_profit_figures", figures / "Profit_Increment.py", []),
        ("09_example_figure", figures / "Classification_Test/plot_classification_example.py", []),
        ("10_verify_rolling", program / "Experiment_Data_1hour.py", ["--experiment", str(program / "experiment_config_1hour.json"), "--verify-results", "rolling"]),
    ]
    child_environment = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    status = {"run_id": run_id, "status": "running", "stages": []}

    def save_status():
        temporary = log_dir / "status.tmp"
        temporary.write_text(json.dumps(status, indent=2), encoding="utf-8")
        temporary.replace(log_dir / "status.json")

    print(f"Logs: {log_dir}", flush=True)
    for name, script, arguments in stages:
        command = [sys.executable, "-u", "-B", str(script), *arguments]
        record = {"name": name, "command": command, "status": "running", "started": datetime.now().astimezone().isoformat()}
        status["stages"].append(record)
        save_status()
        started = time.monotonic()
        print(f"Starting {name}", flush=True)
        try:
            with (log_dir / f"{name}.log").open("w", encoding="utf-8") as log:
                completed = subprocess.run(command, cwd=PROJECT_ROOT, env=child_environment, stdout=log, stderr=subprocess.STDOUT, check=False)
            record.update(returncode=completed.returncode, status="complete" if completed.returncode == 0 else "failed")
        except Exception as exc:
            record.update(returncode=1, status="failed", error=str(exc))
        record["elapsed_seconds"] = round(time.monotonic() - started, 3)
        record["finished"] = datetime.now().astimezone().isoformat()
        if record["status"] == "failed":
            status["status"] = "failed"
            save_status()
            print(f"FAILED {name}; see {log_dir / (name + '.log')}", flush=True)
            return 1
        save_status()
        print(f"Completed {name} in {record['elapsed_seconds']} s", flush=True)
    status["status"] = "complete"
    save_status()
    print("Micro pipeline complete", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
