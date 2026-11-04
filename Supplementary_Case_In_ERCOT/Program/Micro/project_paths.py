"""Canonical locations for Micro inputs, programs, results, and figures.

Paths are anchored to this file so CLI entry points work from any directory.
Experiment configuration paths remain relative to the configuration itself.
"""

from pathlib import Path

PROGRAM_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PROGRAM_DIR.parents[1]
MICRO_DATA_DIR = PROJECT_ROOT / "Data" / "Micro"
MICRO_RESULTS_DIR = PROJECT_ROOT / "Results" / "Micro_Results"
MICRO_FIGS_DIR = PROJECT_ROOT / "Figs" / "Micro"
DEFAULT_EXPERIMENT_PATH = PROGRAM_DIR / "experiment_config_1hour.json"
