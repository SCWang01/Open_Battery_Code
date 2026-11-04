"""Export coal-based Figure 4d from the six 2026 coal system summaries.

Run this script from the project root. --results-dir and --output-root override
the coal defaults directly. Output is one transparent 300 x 110 mm, 720 dpi PNG.
"""

from pathlib import Path

from plot_reduction_rates_radial import main as _main, run_cli


def main(results_dir: Path | None = None, output_root: Path | None = None) -> Path:
    """Export the coal figure, preserving the common Actual/Rolling rate definitions."""
    return _main(results_dir, output_root, basis="coal")


if __name__ == "__main__":
    run_cli("coal", description=__doc__)
