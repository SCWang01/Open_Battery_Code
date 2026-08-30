# Figure 5 comparison notes

Core conclusion: Bidding and self-scheduling outcomes are compared across prediction-error settings of 2%, 4%, 6%, 8%, and 10% over the complete 36-month study period.

Figure archetype: quantitative grouped-bar comparisons.

Backend: Python with matplotlib.

Final size: 90 mm × 72 mm per standalone figure.

## Statistical definitions

- Profit increment, cost reduction, and carbon reduction use the weighted 36-month overall rates from the workbook's overall annual-summary row.
- Monthly profit-increment variance is the population variance over all 36 months (`ddof=0`) after expressing monthly values in percentage points. Its unit is pp².
- The 36-month minimum is the lowest monthly `profit_increment_k20` value in each method/error group, expressed as a percentage.
- The 36-month range is `max - min` over the same monthly profit-increment values. Its unit is pp.
- Bars are deterministic summaries rather than means across experimental replicates, so no error bars or significance tests are shown.
- Every bar starts from zero and carries a two-decimal value label.
- Method colors are fixed across all five figures: Bidding `#3E85C5` and Self-scheduling `#FFA579`.

## Output map

- `profit_increment_k20`: weighted overall profit-increment comparison.
- `cost_reduction`: weighted overall cost-reduction comparison.
- `carbon_reduction`: weighted overall carbon-reduction comparison.
- `profit_increment_k20_minimum`: minimum of the 36 monthly profit-increment values.
- `profit_increment_k20_variance`: population variance of the 36 monthly profit-increment values.
- `profit_increment_k20_range_36_months`: maximum minus minimum of the 36 monthly profit-increment values.

The two CSV files in `Figs` preserve the derived summary values and the monthly values used to calculate variance and range.

## QA/export exception

The generic static preflight recommends PDF and TIFF exports. The requested delivery contract explicitly limits output to editable-text SVG and 600-dpi PNG, so absence of PDF/TIFF is an intentional exception rather than a missing deliverable.

## Final QA record

- Inputs: 10 validated workbooks (five prediction-error settings × two methods).
- Monthly coverage: 360 observations in total; every method/error group contains the same consecutive 36 months from 202301 through 202512.
- Source preflight: syntax, font floor, editable SVG settings, 600-dpi raster configuration, data-sampling checks, uncertainty classification, and Python-only backend checks passed.
- Visual inspection: all six figures were inspected at their final 90 mm × 72 mm size; titles, legends, axes, bars, and value annotations are legible without collisions.
- Export inspection: each SVG contains editable text nodes, and each PNG is 2125 × 1700 pixels (the integer-pixel realization of 90 mm × 72 mm at 600 dpi).
