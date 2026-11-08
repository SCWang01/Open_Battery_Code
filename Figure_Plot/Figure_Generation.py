"""Synchronize Results and regenerate the reproducible Figure 4 assets.

The existing Figure 4 scripts retain their own selections and output paths.
This module prepares their canonical inputs, runs the existing processors in
dependency order, and records the resulting inputs and outputs in a manifest.

Usage from the repository root::

    python Figure_Plot/Figure_Generation.py
    python Figure_Plot/Figure_Generation.py --figures 4d 4e --skip-analysis

The default run regenerates all Figure 4 panels.  It does not modify any of the
existing plotting scripts.
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import os
import platform
import runpy
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESULTS_ROOT = PROJECT_ROOT / "Results"
RESULTS_DIR = RESULTS_ROOT / "Bidding"
DATA_DIR = PROJECT_ROOT / "data"
FIGURE_DIR = PROJECT_ROOT / "Figure_Plot"
PROGRAM_DIR = PROJECT_ROOT / "Program"
MANIFEST_PATH = FIGURE_DIR / "Figure_Generation_manifest.json"

FIGURE_CHOICES = ("4a", "4b", "4c", "4d", "4e")
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
    schema_version: int = 2
    status: str = "running"
    figures: tuple[str, ...] = ()
    skip_analysis: bool = False
    python_version: str = field(default_factory=platform.python_version)
    python_implementation: str = field(default_factory=platform.python_implementation)
    commands: list[dict[str, object]] = field(default_factory=list)
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
        help="Use the existing Results/Bidding analysis workbook instead of rebuilding it.",
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
        "path": path.relative_to(PROJECT_ROOT).as_posix(),
        "size": stat.st_size,
        "sha256": sha256(path),
    }
    if source is not None:
        record["source"] = source.relative_to(PROJECT_ROOT).as_posix()
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
    working_directory = (cwd or PROJECT_ROOT).resolve()
    args = tuple(args)
    recorded_command = [
        "python",
        Path(os.path.relpath(script, working_directory)).as_posix(),
        *args,
    ]
    report.commands.append(
        {
            "argv": recorded_command,
            "cwd": working_directory.relative_to(PROJECT_ROOT).as_posix() or ".",
        }
    )
    subprocess.run(
        [sys.executable, str(script), *args],
        cwd=working_directory,
        check=True,
    )


def plotting_settings(relative_script: str) -> dict:
    """Load a plotting module's definitions without executing its main routine."""
    script = require_file(FIGURE_DIR / relative_script, "figure-generation script")
    return runpy.run_path(str(script))


def selected_state_periods(config: GenerationConfig) -> dict[str, list[tuple[str, tuple[tuple[int, int], ...]]]]:
    """Use each current plot's selection and naming rules as the source of truth."""
    periods = {}
    if config.wants_4ab:
        settings = plotting_settings("figure_plot_4_a_b/plot_state_distribution_pies.py")
        if settings["PLOT_SEASONS"]:
            month_sets = [
                settings["select_months"](settings["season_selection_matrix"](season))
                for season in settings["SEASON_MONTHS"]
            ]
        else:
            month_sets = [settings["select_months"]()]
        periods["4ab"] = [
            (settings["period_basename_suffix"](months), tuple(months))
            for months in month_sets
        ]
    if config.wants_4c:
        settings = plotting_settings("figure_plot_4_c/export_hourly_state_counts.py")
        periods["4c"] = [
            (period.output_suffix, period.months)
            for period in settings["selected_periods"]()
        ]
    return periods


def prepare_state_workbooks(config: GenerationConfig, report: GenerationReport) -> None:
    """Refresh labels/widths while retaining each saved dispatch/staircase snapshot."""
    periods = selected_state_periods(config)
    months = sorted({month for groups in periods.values() for _, selected in groups for month in selected})
    for year, month in months:
        filename = f"dsfunction_{calendar.month_name[month]}{year}_exact_V5_k20.xlsx"
        raw_source = RESULTS_DIR / filename
        output = raw_source.with_name(f"{raw_source.stem}_classified_width.xlsx")
        # Raw and classified archives can represent different simulation runs.
        # Repainting must preserve the snapshot already used by the figures.
        source = require_file(output if output.is_file() else raw_source, "monthly dsfunction snapshot")
        report.files.append(file_record(source, role="source dsfunction snapshot"))
        staging_paths: list[Path] = []
        try:
            for step in ("classified", "widths"):
                with tempfile.NamedTemporaryFile(
                    prefix=f".{output.stem}.{step}.", suffix=".xlsx", dir=RESULTS_DIR, delete=False
                ) as temporary:
                    staging_paths.append(Path(temporary.name))
            classified, widths = staging_paths
            run_script(
                PROGRAM_DIR / "classify_p_ess.py",
                report,
                args=("--input", str(source), "--output", str(classified)),
            )
            run_script(
                PROGRAM_DIR / "add_dsfunction_state_array.py",
                report,
                args=("--input", str(classified), "--output", str(widths)),
            )
            widths.replace(output)
        finally:
            for staged in staging_paths:
                staged.unlink(missing_ok=True)
        report.files.append(file_record(output, role="classified-width dsfunction workbook", source=source))
    if config.wants_4c:
        price_months = sorted({month for _, selected in periods["4c"] for month in selected})
        for year, month in price_months:
            price = DATA_DIR / "price" / f"{year}{month:02d} CAISO Average Price.csv"
            report.files.append(file_record(price, role="Figure 4c price input"))


def available_analysis_workbooks() -> list[tuple[str, Path]]:
    """Follow the radial scripts' default scenarios and missing-input policy."""
    settings = plotting_settings("figure_plot_4_d/plot_profit_increment_radial.py")
    return [
        (scenario, path)
        for scenario, path in settings["SCENARIO_WORKBOOKS"].items()
        if path.is_file()
    ]


def prepare_analysis_workbook(config: GenerationConfig, report: GenerationReport) -> Path:
    """Refresh Bidding analysis and record the actual Figure 4d inputs."""
    if not config.skip_analysis:
        run_script(PROGRAM_DIR / "analyze_summary.py", report)
    source = require_file(RESULTS_DIR / ANALYSIS_NAME, "analysis workbook")
    for scenario, workbook in available_analysis_workbooks():
        report.files.append(file_record(workbook, role=f"Figure 4d {scenario} analysis workbook"))
    return source


def expected_outputs(config: GenerationConfig) -> list[Path]:
    """Return the canonical output paths for the selected panels."""
    paths: list[Path] = []
    periods = selected_state_periods(config)
    if config.wants_4ab:
        base = FIGURE_DIR / "figure_plot_4_a_b" / "Figs"
        for period_suffix, _ in periods["4ab"]:
            paths.extend(
                base / f"state_distribution_pies_{period_suffix}{suffix}"
                for suffix in (".png", ".svg", ".pdf", "_source_data.csv")
            )
    if config.wants_4c:
        base = FIGURE_DIR / "figure_plot_4_c"
        paths.append(base / "output" / "P_ESS_state_hourly_counts_selected.xlsx")
        for period_suffix, _ in periods["4c"]:
            stem = f"hourly_state_counts_and_price_{period_suffix}"
            paths.append(base / "output" / f"{stem}_source_data.csv")
            paths.extend(base / "figures" / f"{stem}{suffix}" for suffix in (".png", ".svg", ".pdf"))
    if config.wants_4d:
        for scenario, _ in available_analysis_workbooks():
            base = FIGURE_DIR / "figure_plot_4_d" / "outputs" / scenario
            for directory, stem in (
                ("profit_increment_radial", "profit_increment_rate_radial_2023_2025"),
                ("combined_radial", "profit_carbon_cost_radial_2023_2025"),
                ("carbon_reduction_radial", "carbon_reduction_rate_radial_2023_2025"),
                ("cost_reduction_radial", "cost_reduction_rate_radial_2023_2025"),
            ):
                paths.extend(base / directory / f"{stem}{suffix}" for suffix in (".png", ".svg", ".pdf", ".tiff"))
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
        if config.wants_4ab or config.wants_4c:
            prepare_state_workbooks(config, report)

        if config.wants_4ab:
            run_script(
                FIGURE_DIR
                / "figure_plot_4_a_b"
                / "plot_state_distribution_pies.py",
                report,
            )

        if config.wants_4c:
            figure_dir = FIGURE_DIR / "figure_plot_4_c"
            run_script(figure_dir / "export_hourly_state_counts.py", report)
            run_script(figure_dir / "plot_hourly_state_counts_price.py", report)

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
    except (FigureGenerationError, OSError, ValueError, subprocess.CalledProcessError) as exc:
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
