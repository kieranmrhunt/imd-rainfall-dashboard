# IMD Daily Gridded Rainfall Dashboard

This folder contains a local dashboard built from the India Meteorological Department
0.25 x 0.25 degree daily gridded rainfall binary archive.

## What Is Included

- `index.html` - static dashboard UI.
- `data/dashboard_data.js` - compact browser-ready data for maps, charts, state
  filters, monthly gridded anomalies, and event rankings.
- `data/recent_data.js` - compact rolling payload for the ten latest available
  daily grids and recent weekly/monthly anomalies.
- `data/recent_manifest.json` - latest source date, recent period coverage, and
  a stable content checksum for scheduled updates.
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
- `scripts/update_recent_rainfall.py` - updater for the official IMD real-time
  daily feed and exact calendar-day 1991-2020 normals.

## Source

IMD source page:
https://www.imdpune.gov.in/cmpg/Griddata/Rainfall_25_Bin.html

Equivalent IMD NetCDF page:
https://www.imdpune.gov.in/cmpg/Griddata/Rainfall_25_NetCDF.html

IMD real-time daily rainfall page:
https://imdpune.gov.in/cmpg/Realtimedata/Rainfall/Rain_Download.html

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

The dashboard separates recent conditions, absolute totals, trend views,
anomaly maps, and extremes. Monthly and daily extremes drive the corresponding
gridded maps, with compressed annual daily-map assets loaded only when needed.

The Recent view uses the official real-time daily binary grids. Its seven-day
and calendar-month anomalies subtract the mean accumulation for the same
calendar dates in 1991-2020. The current month is labelled month-to-date, and
missing source days are reported rather than filled with zero.

## Refresh

```powershell
python scripts/download_imd_rainfall.py --start 1901 --end 2025 --out-dir raw
python scripts/process_imd_rainfall.py --raw-dir raw --data-dir data --boundary assets/india_adm0_simplified.geojson --state-boundary assets/india_adm1_simplified.geojson
```

For the rolling Recent payload:

```powershell
python scripts/update_recent_rainfall.py
```

The updater caches the 1991-2020 daily map assets and real-time IMD grids under
`runtime/`, writes outputs atomically, and leaves the published files untouched
when the underlying data have not changed.

## Scheduled Recent Updates

`scripts/bootstrap_recent_updater.sh` prepares a dedicated Linux checkout,
NumPy virtual environment, and cache. `scripts/crontab.example` uses `flock` and
checks the feed twice each evening in India; only a changed payload is committed
and pushed. The Git remote must already be able to push non-interactively,
normally through an SSH key.

## Related Tooling

The IMDlib project is a more general Python package for downloading and handling
IMD binary gridded meteorological data:
https://github.com/iamsaswata/imdlib

The Pratiman Patel tutorial/blog page includes IMDlib examples:
https://pratiman-91.github.io/blog.html
