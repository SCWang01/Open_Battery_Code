# Monthly Usage Data (Including the First Day of the Next Month)

- Filenames: `<Resource Name>_Usage_<YYYYMM>.xlsx`, covering 24 members with one file per member for each month from January through June 2026.
- Each file's `Usage` sheet contains the available records for the current month and all records for the first day of the next month. The time range runs from 00:00 on the first day of the current month (inclusive) to 00:00 on the second day of the next month (exclusive), using ERCOT local time, America/Chicago.
- All 202 columns from the member Usage source files are retained in their original order. Timestamps are stored as Excel datetime values. The `SCED Timestamp UTC` column uses UTC separately.
- Retain the last actual SCED record in each five-minute interval. The standard timestamp is the interval start; prices are matched exactly using the actual SCED timestamp and repeated-hour flag.
- The additional day for January–May comes from existing member Usage files. July 1, appended to June, comes from `60d_ESR_Data_in_SCED-30-AUG-26.csv`, with prices from `ERCOT_ESR_SCED_LMP_20260601_20260706.csv`.
- Each additional day has 288 five-minute records with no missing prices. Adjacent monthly files overlap by one day; subsequent merges should deduplicate by member, standard timestamp, and repeated-hour flag.
- Original data files remain unchanged. Existing gaps are neither interpolated nor filled with zeros. Empty cells indicate missing data; `NA` in status fields remains text.
- January records for UTOPIA_ESR1 begin on January 7. Other existing minute-level gaps and missing March prices are retained. Member-to-node assignments use the existing mapping snapshot.
- `monthly_with_next_day_index.csv` lists each file's original monthly row count, appended row count, start/end times, gap count, missing-price count, and sources. Expected row counts account for the March daylight saving transition.

Historical generation command (the exporter is not included in this repository; the prepared data are under `Data/Micro/Six_Month_Data/`):

```powershell
python Temp_Export_Monthly_With_Next_Day.py --workers 4
```

After export, the script reads the workbooks back and validates every row's timestamp, member, repeated-hour flag, node, and price. It also checks every column in the first, last, and month-boundary rows. Outputs with the same names are replaced only after validation succeeds.
