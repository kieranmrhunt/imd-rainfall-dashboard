# IMD Daily Gridded Rainfall Dashboard

This folder contains a local dashboard built from the India Meteorological Department
0.25 x 0.25 degree daily gridded rainfall binary archive.

## What Is Included

- `index.html` - static dashboard UI.
- `data/dashboard_data.js` - compact browser-ready data for maps, charts, state
  filters, monthly gridded anomalies, and event rankings.
- `data/annual_summary.csv` - all-India annual and JJAS summary metrics.
- `data/monthly_by_year.csv` - area-weighted all-India monthly totals by year.
- `data/manifest.json` - source, grid, baseline, and generation metadata.
- `assets/india_adm0_simplified.*` - India boundary used for the map outline.
- `assets/india_adm1_simplified.*` - state and union territory boundaries used
  for the state selector and map overlay.
- `raw/rainfall_YYYY.grd` - downloaded IMD binary rainfall files for 1901-2025 when
  the refresh script is run locally. The raw archive is not committed to GitHub.
- `scripts/download_imd_rainfall.py` - resumable downloader with byte-count checks.
- `scripts/process_imd_rainfall.py` - processor for dashboard data products.

## Source

IMD source page:
https://www.imdpune.gov.in/cmpg/Griddata/Rainfall_25_Bin.html

Equivalent IMD NetCDF page:
https://www.imdpune.gov.in/cmpg/Griddata/Rainfall_25_NetCDF.html

The dashboard uses the binary archive because it can be read directly with NumPy
without additional NetCDF dependencies.

Boundary data:
https://www.geoboundaries.org/

## Method

The IMD grid has 135 longitude points and 129 latitude points from 6.5N, 66.5E
to 38.5N, 100.0E. Rainfall is in mm and missing cells use `-999.0`.

Daily means are area-weighted using cosine-latitude weights over valid IMD grid
cells. State and union territory products use geoBoundaries ADM1 polygons to
select grid-cell centres within each region. Annual, JJAS, and monthly totals
are daily sums of those area-weighted means.

Normals and anomalies use the 1991-2020 baseline. Wettest and driest days are
ranked by anomaly against the selected region's baseline mean daily rainfall for
that calendar month. Wettest and driest months are ranked by anomaly against the
selected region's baseline monthly mean. Percentage anomaly is calculated as
`100 * (rainfall - normal) / normal`.

The dashboard separates absolute totals, trend views, anomaly maps, and extremes.
Monthly extremes can drive gridded monthly anomaly maps, so queries such as a
state's wettest September can be inspected spatially. Daily extremes are ranked
from the regional daily series; the map panel shows the corresponding monthly
context because daily gridded maps are not shipped to keep the static site small.

## Refresh

```powershell
python scripts/download_imd_rainfall.py --start 1901 --end 2025 --out-dir raw
python scripts/process_imd_rainfall.py --raw-dir raw --data-dir data --boundary assets/india_adm0_simplified.geojson --state-boundary assets/india_adm1_simplified.geojson
```

## Related Tooling

The IMDlib project is a more general Python package for downloading and handling
IMD binary gridded meteorological data:
https://github.com/iamsaswata/imdlib

The Pratiman Patel tutorial/blog page includes IMDlib examples:
https://pratiman-91.github.io/blog.html
