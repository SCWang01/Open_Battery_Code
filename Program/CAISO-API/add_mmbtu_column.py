"""Add mmbtu_per_mwh column to CAISO natural gas data files."""

from pathlib import Path
import pandas as pd
import numpy as np

def add_mmbtu_column(file_path):
    """Add mmbtu_per_mwh column after Netgen column.

    Args:
        file_path: Path to Excel file

    Returns:
        True if successful, False otherwise
    """
    try:
        # Read the file
        df = pd.read_excel(file_path)

        # Check if column already exists
        if 'mmbtu_per_mwh' in df.columns:
            print(f"  Column already exists in {file_path.name}, skipping...")
            return True

        # Calculate mmbtu_per_mwh
        df['mmbtu_per_mwh'] = df['Elec_MMBtu'] / df['Netgen']

        # Find the position of Netgen column
        netgen_idx = df.columns.get_loc('Netgen')

        # Reorder columns to insert mmbtu_per_mwh after Netgen
        cols = df.columns.tolist()
        cols.remove('mmbtu_per_mwh')
        cols.insert(netgen_idx + 1, 'mmbtu_per_mwh')
        df = df[cols]

        # Write back to file
        df.to_excel(file_path, index=False)

        return True
    except Exception as e:
        print(f"  Error processing {file_path.name}: {e}")
        return False

def main():
    """Process all CAISO NG files for 2023-2025."""
    data_dir = Path(__file__).parent / 'data' / 'ng_cost'

    # Find all files for 2023-2025
    files = []
    for year in [2023, 2024, 2025]:
        for month in range(1, 13):
            file_path = data_dir / f'CAISO_NG_Final_{year}_{month:02d}.xlsx'
            if file_path.exists():
                files.append(file_path)

    print(f"Found {len(files)} files to process")
    print("-" * 60)

    success_count = 0
    fail_count = 0

    for file_path in files:
        print(f"Processing: {file_path.name}")
        if add_mmbtu_column(file_path):
            success_count += 1
        else:
            fail_count += 1

    print("-" * 60)
    print(f"\nSummary:")
    print(f"  Successfully processed: {success_count} files")
    print(f"  Failed: {fail_count} files")
    print(f"  Total: {len(files)} files")

if __name__ == '__main__':
    main()
