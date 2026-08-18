# -*- coding: utf-8 -*-
"""Generate and load the fixed price-error scenarios used by V5.

The complete 2023-01 through 2025-12 case study is generated from one
continuous MT19937 random stream.  Each month is stored separately so a
single-month run and a distributed run consume exactly the same samples as a
full sequential run.
"""

import argparse
import calendar
import json
import os
from pathlib import Path

import numpy as np


SEED = 42
N_T = 24
START_YEAR_MONTH = (2023, 1)
END_YEAR_MONTH = (2025, 12)
SCHEMA_VERSION = 1
RNG_ALGORITHM = 'numpy.random.RandomState(MT19937)'
SAMPLE_SEMANTICS = 'standard_normal_price_error_innovations'

PROGRAM_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PROGRAM_DIR.parent
RANDOM_DATA_DIR = PROJECT_ROOT / 'data' / 'random_data'
MANIFEST_FILENAME = 'manifest.json'


def iter_year_months(start=START_YEAR_MONTH, end=END_YEAR_MONTH):
    """Return inclusive ``(year, month)`` pairs in chronological order."""
    if start > end:
        raise ValueError(f'start month {start} must not follow end month {end}')

    months = []
    year, month = start
    while (year, month) <= end:
        months.append((year, month))
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    return months


YEAR_MONTHS = tuple(iter_year_months())


def year_month_key(year, month):
    """Normalize a year and month to the canonical ``YYYYMM`` key."""
    year = int(year)
    month = int(month)
    if month < 1 or month > 12:
        raise ValueError(f'month must be in 1..12, got {month}')
    return f'{year:04d}{month:02d}'


def expected_shape(year, month):
    """Return the required scenario-array shape for one calendar month."""
    days = calendar.monthrange(int(year), int(month))[1]
    return days * N_T, N_T


def random_data_path(year, month, data_dir=RANDOM_DATA_DIR):
    """Return the canonical path of one month's standard-normal samples."""
    key = year_month_key(year, month)
    return Path(data_dir) / f'price_error_z_{key}.npy'


def manifest_path(data_dir=RANDOM_DATA_DIR):
    return Path(data_dir) / MANIFEST_FILENAME


def _atomic_save_array(path, values):
    temporary_path = path.with_suffix(path.suffix + '.tmp')
    try:
        with temporary_path.open('wb') as stream:
            np.save(stream, values, allow_pickle=False)
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _atomic_save_json(path, payload):
    temporary_path = path.with_suffix(path.suffix + '.tmp')
    try:
        with temporary_path.open('w', encoding='utf-8', newline='\n') as stream:
            json.dump(payload, stream, indent=2, ensure_ascii=False)
            stream.write('\n')
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def generate_random_data(data_dir=RANDOM_DATA_DIR, force=False):
    """Generate all monthly files from one continuous seeded random stream.

    Existing canonical files are never overwritten unless ``force`` is true.
    All arrays are generated before writing begins so the RNG stream cannot be
    affected by the set of files already present on disk.
    """
    data_dir = Path(data_dir)
    output_paths = [
        random_data_path(year, month, data_dir)
        for year, month in YEAR_MONTHS
    ]
    canonical_paths = output_paths + [manifest_path(data_dir)]
    existing_paths = [path for path in canonical_paths if path.exists()]
    if existing_paths and not force:
        formatted = '\n  '.join(str(path) for path in existing_paths)
        raise FileExistsError(
            'Refusing to overwrite existing random-data files. '
            'Use --force to rebuild the complete scenario set:\n  '
            f'{formatted}'
        )

    rng = np.random.RandomState(SEED)
    monthly_arrays = []
    manifest_files = []
    for year, month in YEAR_MONTHS:
        shape = expected_shape(year, month)
        samples = rng.standard_normal(size=shape).astype(np.float64, copy=False)
        monthly_arrays.append(samples)
        manifest_files.append({
            'year_month': year_month_key(year, month),
            'filename': random_data_path(year, month, data_dir).name,
            'shape': list(shape),
        })

    data_dir.mkdir(parents=True, exist_ok=True)
    for path, samples in zip(output_paths, monthly_arrays):
        _atomic_save_array(path, samples)

    manifest = {
        'schema_version': SCHEMA_VERSION,
        'seed': SEED,
        'rng_algorithm': RNG_ALGORITHM,
        'sample_semantics': SAMPLE_SEMANTICS,
        'dtype': 'float64',
        'n_t': N_T,
        'start_year_month': year_month_key(*START_YEAR_MONTH),
        'end_year_month': year_month_key(*END_YEAR_MONTH),
        'generation_order': 'year_month_then_c_order',
        'files': manifest_files,
    }
    _atomic_save_json(manifest_path(data_dir), manifest)
    return output_paths, manifest_path(data_dir)


def _load_manifest(data_dir):
    path = manifest_path(data_dir)
    if not path.is_file():
        raise FileNotFoundError(
            f'Random-data manifest not found: {path}. '
            'Run `python Random_Generator.py` first.'
        )
    try:
        with path.open('r', encoding='utf-8') as stream:
            manifest = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f'Cannot read random-data manifest {path}: {exc}') from exc

    expected_metadata = {
        'schema_version': SCHEMA_VERSION,
        'seed': SEED,
        'rng_algorithm': RNG_ALGORITHM,
        'sample_semantics': SAMPLE_SEMANTICS,
        'dtype': 'float64',
        'n_t': N_T,
        'start_year_month': year_month_key(*START_YEAR_MONTH),
        'end_year_month': year_month_key(*END_YEAR_MONTH),
        'generation_order': 'year_month_then_c_order',
    }
    mismatches = {
        key: (manifest.get(key), expected)
        for key, expected in expected_metadata.items()
        if manifest.get(key) != expected
    }
    if mismatches:
        details = ', '.join(
            f'{key}={actual!r} (expected {expected!r})'
            for key, (actual, expected) in mismatches.items()
        )
        raise ValueError(f'Incompatible random-data manifest {path}: {details}')
    return manifest


def load_monthly_innovations(year, month, n_day=None, data_dir=RANDOM_DATA_DIR):
    """Load and strictly validate one month's standard-normal innovations."""
    year = int(year)
    month = int(month)
    key = year_month_key(year, month)
    if (year, month) not in YEAR_MONTHS:
        raise ValueError(
            f'Random data for {key} is outside the canonical '
            f'{year_month_key(*START_YEAR_MONTH)}-'
            f'{year_month_key(*END_YEAR_MONTH)} period'
        )

    data_dir = Path(data_dir)
    manifest = _load_manifest(data_dir)
    entries = {
        entry.get('year_month'): entry
        for entry in manifest.get('files', [])
        if isinstance(entry, dict)
    }
    entry = entries.get(key)
    if entry is None:
        raise ValueError(f'Random-data manifest has no entry for {key}')

    path = random_data_path(year, month, data_dir)
    if entry.get('filename') != path.name:
        raise ValueError(
            f'Random-data manifest filename for {key} is '
            f'{entry.get("filename")!r}, expected {path.name!r}'
        )
    if not path.is_file():
        raise FileNotFoundError(
            f'Random data for {key} not found: {path}. '
            'Run `python Random_Generator.py` first.'
        )

    expected = expected_shape(year, month)
    if n_day is not None and int(n_day) * N_T != expected[0]:
        raise ValueError(
            f'n_day={n_day} is inconsistent with calendar month {key}'
        )
    if entry.get('shape') != list(expected):
        raise ValueError(
            f'Random-data manifest shape for {key} is {entry.get("shape")!r}, '
            f'expected {list(expected)!r}'
        )

    try:
        samples = np.load(path, allow_pickle=False)
    except (OSError, ValueError) as exc:
        raise ValueError(f'Cannot load random data for {key} from {path}: {exc}') from exc
    if samples.shape != expected:
        raise ValueError(
            f'Random data for {key} has shape {samples.shape}, expected {expected}'
        )
    if samples.dtype != np.dtype(np.float64):
        raise ValueError(
            f'Random data for {key} has dtype {samples.dtype}, expected float64'
        )
    if not np.isfinite(samples).all():
        raise ValueError(f'Random data for {key} contains non-finite values')
    return samples


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Generate the fixed 36-month V5 price-error scenario set.'
    )
    parser.add_argument(
        '--force', action='store_true',
        help='replace the complete canonical scenario set if it already exists',
    )
    parser.add_argument(
        '--output-dir', type=Path, default=RANDOM_DATA_DIR,
        help=f'output directory (default: {RANDOM_DATA_DIR})',
    )
    args = parser.parse_args(argv)

    output_paths, output_manifest = generate_random_data(
        data_dir=args.output_dir,
        force=args.force,
    )
    print(
        f'Generated {len(output_paths)} monthly random-data files '
        f'using one {RNG_ALGORITHM} stream with seed {SEED}.'
    )
    print(f'Wrote manifest: {output_manifest}')
    return output_manifest


if __name__ == '__main__':
    main()
