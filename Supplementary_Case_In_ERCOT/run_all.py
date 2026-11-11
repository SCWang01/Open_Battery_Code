"""Recompute distributed Rolling, all Micro figures and thermal Macro figures.

Run ``python -u -B run_all.py --workers 4`` from any working directory, or use
an editor's Run button (12 workers by default). Inputs and outputs follow the
release's experiment_config_1hour.json. This replaces active result/figure files.
Macro is thermal only; Figure 4e reads the newly generated thermal workbook.
Use --dry-run to review commands, or --postprocess-only after a complete Rolling
run under the current SOC policy. Partial/stale Rolling runs fail verification.
"""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "Program/Micro"))

from run_pipeline import Stage, build_micro_stages, create_parser, run_stages


def build_stages(workers: int, *, postprocess_only: bool = False) -> list[Stage]:
    """Assemble the Micro dependency chain followed by thermal Macro and plots."""
    stages = build_micro_stages(workers, postprocess_only=postprocess_only)
    thermal_results = PROJECT_ROOT / "Results/Macro_Results/System_Results/thermal"
    stages.extend(
        [
            Stage(
                "bridge",
                PROJECT_ROOT / "Program/Micro/Bridge.py",
                (
                    "--output-dir",
                    str(PROJECT_ROOT / "Results/Macro_Results/ESR_Output_Rolling"),
                ),
            ),
            Stage(
                "macro_thermal",
                PROJECT_ROOT / "Program/Macro/System_Rolling_Result.py",
                ("--basis", "thermal"),
            ),
            Stage(
                "figure_4d_thermal",
                PROJECT_ROOT
                / "Figs/Macro/figure_plot_4_d/plot_thermal_reduction_rates_radial.py",
            ),
            Stage(
                "figure_4e",
                PROJECT_ROOT / "Figs/Macro/figure_plot_4_e/Fig_4_e_plot.py",
                ("--results-dir", str(thermal_results)),
            ),
        ]
    )
    return stages


def main() -> int:
    parser = create_parser(__doc__)
    arguments = parser.parse_args()
    try:
        stages = build_stages(
            arguments.workers, postprocess_only=arguments.postprocess_only
        )
        return run_stages(
            stages,
            log_root=PROJECT_ROOT / "Results/pipeline_runs",
            dry_run=arguments.dry_run,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
