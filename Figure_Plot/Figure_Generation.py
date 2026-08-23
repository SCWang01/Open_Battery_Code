"""Synchronize Results and regenerate the reproducible Figure 4 assets.

The existing Figure 4 scripts intentionally keep their figure-specific paths.
This module is the small orchestration layer around them: it resolves canonical
inputs, copies fresh snapshots into those paths, runs the existing processors in
dependency order, and records the resulting inputs and outputs in a manifest.

Usage from the repository root::

    python Figure_Plot/Figure_Generation.py
    python Figure_Plot/Figure_Generation.py --figures 4d 4e --skip-analysis

The default run regenerates all Figure 4 panels.  It does not modify any of the
existing plotting scripts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = PROJECT_ROOT / "Results"
DATA_DIR = PROJECT_ROOT / "data"
FIGURE_DIR = PROJECT_ROOT / "Figure_Plot"
PROGRAM_DIR = PROJECT_ROOT / "Program"
MANIFEST_PATH = FIGURE_DIR / "Figure_Generation_manifest.json"

FIGURE_CHOICES = ("4a", "4b", "4c", "4d", "4e")
MAY_DSFUNCTION_NAME = "dsfunction_May2025_exact_V5_k20.xlsx"
MAY_PRICE_NAME = "202505 CAISO Average Price.csv"
APRIL_RESULT_NAME = "April2025_eta95%_std2_exact_V5_k20.csv"
APRIL_PRICE_NAME = "202504 CAISO Average Price.csv"
APRIL_CURTAILMENT_NAME = "curtailment_202504.csv"
ANALYSIS_NAME = "analysis_202301_202512.xlsx"


class FigureGenerationError(RuntimeError):
    """Raised when a required input or figure-generation command fails."""


@dataclass(frozen=True)
class GenerationConfig:
    """Validated command configuration for one figure-generation run."""

    figures: tuple[str, ...]
    skip_analysis: bool = False

    @property
    def wants_4ab(self) -> bool:
        return bool(set(self.figures) & {"4a", "4b"})

    @property
    def wants_4c(self) -> bool:
        return "4c" in self.figures

    @property
    def wants_4d(self) -> bool:
        return "4d" in self.figures

    @property
    def wants_4e(self) -> bool:
        return "4e" in self.figures


@dataclass
class GenerationReport:
    """Collected provenance and outputs written to the generation manifest."""

    started_at: str
    status: str = "running"
    figures: tuple[str, ...] = ()
    skip_analysis: bool = False
    commands: list[list[str]] = field(default_factory=list)
    files: list[dict[str, object]] = field(default_factory=list)
    outputs: list[dict[str, object]] = field(default_factory=list)
    error: str | None = None
    finished_at: str | None = None


def parse_args(argv: Sequence[str] | None = None) -> GenerationConfig:
    """Parse and validate the public command-line boundary."""
    parser = argparse.ArgumentParser(
        description="Synchronize Results and regenerate Figure 4 assets."
    )
    parser.add_argument(
        "--figures",
        nargs="+",
        choices=FIGURE_CHOICES,
        default=list(FIGURE_CHOICES),
        help="Panels to regenerate (default: all Figure 4 panels).",
    )
    parser.add_argument(
        "--skip-analysis",
        action="store_true",
        help="Use the existing Results analysis workbook instead of rebuilding it.",
    )
    args = parser.parse_args(argv)
    figures = tuple(dict.fromkeys(args.figures))
    if not figures:
        raise FigureGenerationError("At least one figure panel must be selected.")
    return GenerationConfig(figures=figures, skip_analysis=args.skip_analysis)


def require_file(path: Path, description: str) -> Path:
    """Validate a required filesystem input and return its normalized path."""
    if not path.is_file():
        raise FigureGenerationError(f"Missing {description}: {path}")
    return path.resolve()


def resolve_source(filename: str, description: str, *directories: Path) -> Path:
    """Resolve one source file using the declared directory priority."""
    searched = [directory / filename for directory in directories]
    for candidate in searched:
        if candidate.is_file():
            return candidate.resolve()
    locations = ", ".join(str(path) for path in searched)
    raise FigureGenerationError(f"Missing {description}; searched: {locations}")


def sha256(path: Path) -> str:
    """Return a stable content hash for a file recorded in the manifest."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_record(path: Path, *, role: str, source: Path | None = None) -> dict[str, object]:
    """Build one provenance record after validating a generated or copied file."""
    path = require_file(path, role)
    stat = path.stat()
    record: dict[str, object] = {
        "role": role,
        "path": str(path.relative_to(PROJECT_ROOT)),
        "size": stat.st_size,
        "sha256": sha256(path),
    }
    if source is not None:
        record["source"] = str(source.relative_to(PROJECT_ROOT))
    return record


def sync_file(source: Path, destination: Path, report: GenerationReport, role: str) -> Path:
    """Copy a canonical source snapshot into a legacy figure input location."""
    source = require_file(source, role)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.is_file() or sha256(source) != sha256(destination):
        shutil.copy2(source, destination)
        action = "copied"
    else:
        action = "unchanged"
    report.files.append(
        {
            **file_record(destination, role=role, source=source),
            "action": action,
        }
    )
    return destination


def run_script(
    script: Path,
    report: GenerationReport,
    *,
    cwd: Path | None = None,
    args: Iterable[str] = (),
) -> None:
    """Run an existing repository script with a checked subprocess boundary."""
    script = require_file(script, "figure-generation script")
    command = [sys.executable, str(script), *args]
    report.commands.append(command)
    subprocess.run(command, cwd=cwd or PROJECT_ROOT, check=True)


def prepare_may_workbook(report: GenerationReport) -> Path:
    """Refresh, classify, and return the May workbook used by Figures 4a-c."""
    source = resolve_source(
        MAY_DSFUNCTION_NAME,
        "May 2025 dsfunction workbook",
        RESULTS_DIR,
    )
    source_dir = FIGURE_DIR / "figure_plot_4_a_b" / "source_data"
    workbook = sync_file(
        source,
        source_dir / MAY_DSFUNCTION_NAME,
        report,
        "May dsfunction source workbook",
    )
    run_script(
        source_dir / "classify_p_ess.py",
        report,
        cwd=source_dir,
        args=("--input", workbook.name),
    )
    classified = source_dir / f"{Path(MAY_DSFUNCTION_NAME).stem}_classified.xlsx"
    # Figure 4a-b consumes the derived interval-width column; this existing
    # preprocessing step must run after classification and before plotting.
    run_script(source_dir / "add_dsfunction_state_array.py", report, cwd=source_dir)
    report.files.append(file_record(classified, role="classified May dsfunction workbook"))
    return require_file(classified, "classified May dsfunction workbook")


def prepare_analysis_workbook(config: GenerationConfig, report: GenerationReport) -> Path:
    """Refresh and synchronize the Figure 4d analysis workbook."""
    if not config.skip_analysis:
        run_script(PROGRAM_DIR / "analyze_summary.py", report)
    source = require_file(RESULTS_DIR / ANALYSIS_NAME, "analysis workbook")
    destination = FIGURE_DIR / "figure_plot_4_d" / ANALYSIS_NAME
    return sync_file(source, destination, report, "Figure 4d analysis workbook")


def expected_outputs(config: GenerationConfig) -> list[Path]:
    """Return the canonical output paths for the selected panels."""
    paths: list[Path] = []
    if config.wants_4ab:
        base = FIGURE_DIR / "figure_plot_4_a_b" / "state_distribution_pies_May2025"
        paths.extend(
            base / f"state_distribution_pies_May2025{suffix}"
            for suffix in (".png", ".svg", ".pdf", "_source_data.csv")
        )
    if config.wants_4c:
        base = FIGURE_DIR / "figure_plot_4_c"
        paths.extend(
            [
                base / "output" / "P_ESS_state_hourly_counts_May2025.xlsx",
                base / "output" / "hourly_state_counts_and_price_May2025_source_data.csv",
                *(base / "figures" / f"hourly_state_counts_and_price_May2025{suffix}" for suffix in (".png", ".svg", ".pdf")),
            ]
        )
    if config.wants_4d:
        base = FIGURE_DIR / "figure_plot_4_d" / "outputs"
        paths.extend(
            base / "profit_increment_radial" / f"profit_increment_rate_radial_2023_2025{suffix}"
            for suffix in (".png", ".svg", ".pdf", ".tiff")
        )
        paths.extend(
            base / "combined_radial" / f"profit_carbon_cost_radial_2023_2025{suffix}"
            for suffix in (".png", ".svg", ".pdf", ".tiff")
        )
        paths.extend(
            base / "carbon_reduction_radial" / f"carbon_reduction_rate_radial_2023_2025{suffix}"
            for suffix in (".png", ".svg", ".pdf", ".tiff")
        )
        paths.extend(
            base / "cost_reduction_radial" / f"cost_reduction_rate_radial_2023_2025{suffix}"
            for suffix in (".png", ".svg", ".pdf", ".tiff")
        )
    if config.wants_4e:
        base = FIGURE_DIR / "figure_plot_4_e" / "Figs"
        paths.extend(base / f"Fig_4_e{suffix}" for suffix in (".png", ".svg", ".pdf"))
    return paths


def collect_outputs(config: GenerationConfig) -> list[dict[str, object]]:
    """Validate and hash every output promised by the selected workflows."""
    missing = [path for path in expected_outputs(config) if not path.is_file()]
    if missing:
        formatted = ", ".join(str(path) for path in missing)
        raise FigureGenerationError(f"Expected figure output was not created: {formatted}")
    return [
        file_record(path, role="generated figure output")
        for path in expected_outputs(config)
    ]


def generate(config: GenerationConfig) -> GenerationReport:
    """Execute the dependency-ordered Figure 4 generation workflow."""
    report = GenerationReport(
        started_at=datetime.now(timezone.utc).isoformat(),
        figures=config.figures,
        skip_analysis=config.skip_analysis,
    )
    try:
        classified_workbook: Path | None = None
        if config.wants_4ab or config.wants_4c:
            classified_workbook = prepare_may_workbook(report)

        if config.wants_4ab:
            run_script(
                FIGURE_DIR
                / "figure_plot_4_a_b"
                / "state_distribution_pies_May2025"
                / "plot_state_distribution_pies_May2025.py",
                report,
            )

        if config.wants_4c:
            if classified_workbook is None:
                raise FigureGenerationError(
                    "Figure 4c requires the classified May dsfunction workbook."
                )
            figure_dir = FIGURE_DIR / "figure_plot_4_c"
            sync_file(
                classified_workbook,
                figure_dir / "input" / f"{Path(MAY_DSFUNCTION_NAME).stem}_classified.xlsx",
                report,
                "Figure 4c classified dsfunction workbook",
            )
            may_price = resolve_source(
                MAY_PRICE_NAME,
                "May 2025 price CSV",
                RESULTS_DIR,
                DATA_DIR / "price",
            )
            sync_file(
                may_price,
                figure_dir / "input" / MAY_PRICE_NAME,
                report,
                "Figure 4c price input",
            )
            run_script(figure_dir / "export_hourly_state_counts.py", report)
            run_script(figure_dir / "plot_hourly_state_counts_price_May2025.py", report)

        if config.wants_4d:
            prepare_analysis_workbook(config, report)
            figure_dir = FIGURE_DIR / "figure_plot_4_d"
            run_script(figure_dir / "plot_profit_increment_radial.py", report)
            run_script(figure_dir / "plot_reduction_rates_radial.py", report)

        if config.wants_4e:
            figure_dir = FIGURE_DIR / "figure_plot_4_e"
            sync_file(
                RESULTS_DIR / APRIL_RESULT_NAME,
                figure_dir / "input" / APRIL_RESULT_NAME,
                report,
                "Figure 4e April result CSV",
            )
            april_price = resolve_source(
                APRIL_PRICE_NAME,
                "April 2025 price CSV",
                RESULTS_DIR,
                DATA_DIR / "price",
            )
            sync_file(
                april_price,
                figure_dir / "input" / APRIL_PRICE_NAME,
                report,
                "Figure 4e price input",
            )
            april_curtailment = resolve_source(
                APRIL_CURTAILMENT_NAME,
                "April 2025 curtailment CSV",
                RESULTS_DIR,
                DATA_DIR / "curtailment",
            )
            sync_file(
                april_curtailment,
                figure_dir / "input" / APRIL_CURTAILMENT_NAME,
                report,
                "Figure 4e curtailment input",
            )
            run_script(
                figure_dir / "Fig_4_e_plot.py",
                report,
                args=("--date", "2025-04-07"),
            )

        report.outputs = collect_outputs(config)
        report.status = "success"
    except (FigureGenerationError, OSError, subprocess.CalledProcessError) as exc:
        report.status = "failed"
        report.error = str(exc)
        raise FigureGenerationError(str(exc)) from exc
    finally:
        report.finished_at = datetime.now(timezone.utc).isoformat()
        MANIFEST_PATH.write_text(
            json.dumps(asdict(report), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and expose stable success/failure exit codes."""
    try:
        config = parse_args(argv)
        report = generate(config)
    except (FigureGenerationError, OSError) as exc:
        print(f"Figure generation failed: {exc}", file=sys.stderr)
        return 1

    print(
        f"Generated Figure 4 panels {', '.join(report.figures)}; "
        f"manifest: {MANIFEST_PATH}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
