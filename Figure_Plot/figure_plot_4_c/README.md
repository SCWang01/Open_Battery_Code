# Figure C: hourly P_ESS states and CAISO prices

This directory contains the reproducible workflow for Figure C. The final figure combines:

- stacked bars showing the number of May 2025 days assigned to each `P_ESS_state` at every clock hour; and
- a CAISO electricity-price line showing the average hourly price across May 2025, with a two-sided 90% Student's *t* confidence interval.

The workflow uses Python and writes the final figure to the `figures/` directory.

## Files

### Input data

- `input/dsfunction_May2025_exact_V5_k20_classified.xlsx` - classified P_ESS records. The `May2025` worksheet must contain the columns `time` and `P_ESS_state`.
- `input/202505 CAISO Average Price.csv` - five-minute CAISO prices with the columns `date` and `price`.

### Scripts and intermediate data

- [`export_hourly_state_counts.py`](export_hourly_state_counts.py) - aggregates the classified records by clock hour and P_ESS state.
- `output/P_ESS_state_hourly_counts_May2025.xlsx` - generated intermediate workbook containing the 24 hourly state-count distributions.
- [`plot_hourly_state_counts_price_May2025.py`](plot_hourly_state_counts_price_May2025.py) - calculates the price summaries, writes source data, and renders the figure.
- `output/hourly_state_counts_and_price_May2025_source_data.csv` - generated table containing the values displayed in the figure.

### Figure outputs

- `figures/hourly_state_counts_and_price_May2025.png` - transparent PNG at 720 dpi.
- `figures/hourly_state_counts_and_price_May2025.svg` - vector graphic with editable text.
- `figures/hourly_state_counts_and_price_May2025.pdf` - vector PDF suitable for submission or layout.

## Requirements

Python 3.12 or a compatible recent Python 3 release is recommended. Install the required packages in a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install matplotlib openpyxl scipy
```

The scripts also use Python standard-library modules such as `csv`, `datetime`, `math`, and `pathlib`.

## Recreate the figure

Run the following commands from this directory:

```powershell
python .\export_hourly_state_counts.py
python .\plot_hourly_state_counts_price_May2025.py
```

Run [`export_hourly_state_counts.py`](export_hourly_state_counts.py) first because [`plot_hourly_state_counts_price_May2025.py`](plot_hourly_state_counts_price_May2025.py) reads the generated `output/P_ESS_state_hourly_counts_May2025.xlsx` file. Both scripts use paths relative to their own location, so the commands can also be launched from another working directory by providing the script path. The scripts create the `output/` and `figures/` directories when needed.

The plotting script overwrites the existing source-data CSV in `output/` and the three files with the same basename in `figures/`.

## Data-processing procedure

### 1. Hourly P_ESS state counts

[`export_hourly_state_counts.py`](export_hourly_state_counts.py) reads the `May2025` worksheet and groups records by `timestamp.hour` and `P_ESS_state`.

The script expects 744 records in total:

- 31 days x 24 clock hours = 744 records;
- 31 observations must be present for every clock hour.

The displayed stack contains the states in this bottom-to-top order:

`MC`, `CC`, `CD`, `NU`, `DC`, `DD`, `MD`

The source data may also contain `NC`. The plotting script checks that all `NC` counts are zero and then omits `NC` from the displayed stack. It also checks that the seven displayed states sum to 31 for every hour.

### 2. Hourly CAISO price summaries

[`plot_hourly_state_counts_price_May2025.py`](plot_hourly_state_counts_price_May2025.py) reads the price CSV, retains only timestamps from May 2025, and checks that there are 12 five-minute observations for every day-hour combination:

31 days x 24 hours x 12 observations = 8,928 May price records.

For each day `d` and hour `h`, the 12 five-minute prices are first averaged:

```text
daily_hourly_mean[d, h] = mean of the 12 five-minute prices for day d and hour h
```

For each clock hour, the 31 daily hourly means are then summarized as:

```text
hourly_mean[h] = mean of the 31 daily hourly means
```

The shaded band is a two-sided 90% Student's *t* confidence interval calculated from those 31 daily hourly means:

```text
hourly_mean[h] +/- t(0.95, 30) x sample_SD[h] / sqrt(31)
```

Thus, the confidence interval has 31 daily observations and 30 degrees of freedom. Non-May rows, if present in the price CSV, are excluded and reported in the console output.

## Figure construction

- The left y-axis shows state counts from 0 to 35.
- The right y-axis shows price from 0 to 60 USD/MWh.
- The x-axis contains the 24 clock hours, from 00:00 to 23:00.
- State counts are drawn as stacked bars.
- The average price is drawn as a magenta line with diamond markers.
- The 90% confidence interval is drawn as a semi-transparent magenta hatched band.
- The figure canvas is 180 mm x 64 mm with a transparent background.

The generated source-data CSV contains the seven state counts, the hourly average price, the lower and upper 90% confidence limits, the daily-mean standard deviation, the number of days, and the number of five-minute observations per day-hour. It can therefore be used to audit the plotted values without reading the image files.

## Reproducibility checks

The scripts stop with an error if any of the following checks fail:

- required worksheets or columns are missing;
- a state count is negative, non-integer, duplicated, or missing;
- an hourly state total is not 31;
- `NC` has a non-zero count;
- a price is invalid or non-finite;
- the May price record count is not 8,928; or
- a day-hour combination does not contain exactly 12 five-minute prices.

The console output reports the number of included and excluded price rows, the confidence level, the price and confidence-interval ranges, and the names of all generated files.
