"""Process IMD daily gridded rainfall binaries into dashboard data."""

from __future__ import annotations

import argparse
import base64
import calendar
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


NLON = 135
NLAT = 129
LON0 = 66.5
LAT0 = 6.5
DX = 0.25
MISSING = -999.0
UINT16_MISSING = 65535
BASELINE_START = 1991
BASELINE_END = 2020


@dataclass(frozen=True)
class YearProduct:
    year: int
    dates: pd.DatetimeIndex
    daily_mean: np.ndarray
    annual_grid: np.ndarray
    jjas_grid: np.ndarray
    monthly_totals: np.ndarray
    annual_total: float
    jjas_total: float
    wet_area_fraction: float
    wettest_day: str
    wettest_day_mean: float
    valid_cells: int


def days_in_year(year: int) -> int:
    return 366 if calendar.isleap(year) else 365


def expected_values(year: int) -> int:
    return days_in_year(year) * NLAT * NLON


def read_grid(path: Path, year: int) -> np.ndarray:
    arr = np.fromfile(path, dtype="<f4")
    expected = expected_values(year)
    if arr.size != expected:
        raise ValueError(f"{path.name}: expected {expected} float32 values, got {arr.size}")
    return arr.reshape((days_in_year(year), NLAT, NLON))


def weighted_mean_daily(arr: np.ndarray, valid: np.ndarray, weights: np.ndarray) -> np.ndarray:
    weighted = np.where(valid, arr, 0.0) * weights
    denom = (valid * weights).sum(axis=(1, 2))
    return weighted.sum(axis=(1, 2)) / denom


def process_year(path: Path, year: int, weights: np.ndarray) -> YearProduct:
    arr = read_grid(path, year)
    dates = pd.date_range(f"{year}-01-01", periods=days_in_year(year), freq="D")
    valid = np.isfinite(arr) & (arr > MISSING / 2.0)

    daily_mean = weighted_mean_daily(arr, valid, weights)
    annual_grid = np.where(valid, arr, 0.0).sum(axis=0)
    annual_grid[valid.sum(axis=0) == 0] = np.nan

    jjas_mask = np.asarray(dates.month.isin([6, 7, 8, 9]))
    jjas_valid = valid[jjas_mask]
    jjas_grid = np.where(jjas_valid, arr[jjas_mask], 0.0).sum(axis=0)
    jjas_grid[jjas_valid.sum(axis=0) == 0] = np.nan

    monthly_totals = np.zeros(12, dtype=np.float64)
    month_index = dates.month.to_numpy()
    for month in range(1, 13):
        monthly_totals[month - 1] = daily_mean[month_index == month].sum()

    wet_fraction = (((arr >= 1.0) & valid) * weights).sum(axis=(1, 2)) / (valid * weights).sum(axis=(1, 2))
    wettest_idx = int(np.nanargmax(daily_mean))

    return YearProduct(
        year=year,
        dates=dates,
        daily_mean=daily_mean.astype(np.float32),
        annual_grid=annual_grid.astype(np.float32),
        jjas_grid=jjas_grid.astype(np.float32),
        monthly_totals=monthly_totals.astype(np.float32),
        annual_total=float(daily_mean.sum()),
        jjas_total=float(daily_mean[jjas_mask].sum()),
        wet_area_fraction=float(np.nanmean(wet_fraction)),
        wettest_day=dates[wettest_idx].strftime("%Y-%m-%d"),
        wettest_day_mean=float(daily_mean[wettest_idx]),
        valid_cells=int(np.isfinite(annual_grid).sum()),
    )


def encode_uint16(values: np.ndarray, scale: float = 1.0) -> str:
    scaled = np.where(np.isfinite(values), np.rint(values * scale), UINT16_MISSING)
    scaled = np.clip(scaled, 0, UINT16_MISSING).astype("<u2", copy=False)
    return base64.b64encode(scaled.tobytes()).decode("ascii")


def encode_int16(values: np.ndarray, scale: float = 1.0) -> str:
    scaled = np.where(np.isfinite(values), np.rint(values * scale), -32768)
    scaled = np.clip(scaled, -32768, 32767).astype("<i2", copy=False)
    return base64.b64encode(scaled.tobytes()).decode("ascii")


def round_list(values: np.ndarray, digits: int = 1) -> list[float]:
    return [round(float(v), digits) for v in values]


def nanmean_stack(arrays: list[np.ndarray]) -> np.ndarray:
    stack = np.stack(arrays).astype(np.float64)
    finite = np.isfinite(stack)
    totals = np.where(finite, stack, 0.0).sum(axis=0)
    counts = finite.sum(axis=0)
    out = np.full(totals.shape, np.nan, dtype=np.float64)
    np.divide(totals, counts, out=out, where=counts > 0)
    return out


def build_data(products: list[YearProduct], boundary_path: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    years = [p.year for p in products]
    lats = LAT0 + np.arange(NLAT) * DX
    lons = LON0 + np.arange(NLON) * DX
    lon2d, lat2d = np.meshgrid(lons, lats)

    baseline = [p for p in products if BASELINE_START <= p.year <= BASELINE_END]
    if len(baseline) < 10:
        baseline = products
    baseline_label = f"{baseline[0].year}-{baseline[-1].year}"

    clim_annual = nanmean_stack([p.annual_grid for p in baseline])
    clim_jjas = nanmean_stack([p.jjas_grid for p in baseline])
    valid_points = np.isfinite(clim_annual)
    valid_lons = lon2d[valid_points]
    valid_lats = lat2d[valid_points]

    annual_maps: dict[str, str] = {}
    jjas_maps: dict[str, str] = {}
    for product in products:
        annual_maps[str(product.year)] = encode_uint16(product.annual_grid[valid_points])
        jjas_maps[str(product.year)] = encode_uint16(product.jjas_grid[valid_points])

    daily_by_year = {
        str(p.year): encode_uint16(np.maximum(p.daily_mean, 0.0), scale=10.0)
        for p in products
    }
    daily_lengths = {str(p.year): len(p.daily_mean) for p in products}

    monthly_by_year = {str(p.year): round_list(p.monthly_totals, 1) for p in products}
    monthly_clim = round_list(np.nanmean(np.stack([p.monthly_totals for p in baseline]), axis=0), 1)

    annual_rows = []
    for product in products:
        annual_rows.append(
            {
                "year": product.year,
                "annual_mm": round(product.annual_total, 1),
                "jjas_mm": round(product.jjas_total, 1),
                "jjas_share": round(100.0 * product.jjas_total / product.annual_total, 1),
                "wet_area_fraction": round(100.0 * product.wet_area_fraction, 1),
                "wettest_day": product.wettest_day,
                "wettest_day_mean_mm": round(product.wettest_day_mean, 1),
                "valid_cells": product.valid_cells,
            }
        )

    annual_series = np.array([p.annual_total for p in products], dtype=np.float64)
    jjas_series = np.array([p.jjas_total for p in products], dtype=np.float64)
    wettest = sorted(annual_rows, key=lambda row: row["wettest_day_mean_mm"], reverse=True)[:8]
    ranking = sorted(annual_rows, key=lambda row: row["annual_mm"], reverse=True)
    for rank, row in enumerate(ranking, start=1):
        row["wet_rank"] = rank
    rank_by_year = {row["year"]: row["wet_rank"] for row in ranking}
    for row in annual_rows:
        row["wet_rank"] = rank_by_year[row["year"]]

    payload = {
        "meta": {
            "title": "IMD Daily Gridded Rainfall Dashboard",
            "source": "India Meteorological Department 0.25 x 0.25 degree daily gridded rainfall binary archive",
            "sourcePage": "https://www.imdpune.gov.in/cmpg/Griddata/Rainfall_25_Bin.html",
            "netcdfPage": "https://www.imdpune.gov.in/cmpg/Griddata/Rainfall_25_NetCDF.html",
            "citation": "Pai D.S., Latha Sridhar, Rajeevan M., Sreejith O.P., Satbhai N.S. and Mukhopadhyay B. (2014), MAUSAM, 65, 1, pp. 1-18.",
            "downloadedYears": [min(years), max(years)],
            "yearCount": len(years),
            "grid": {
                "resolutionDegrees": DX,
                "lonStart": LON0,
                "latStart": LAT0,
                "lonCount": NLON,
                "latCount": NLAT,
                "validCellCount": int(valid_points.sum()),
            },
            "baseline": baseline_label,
            "generated": pd.Timestamp.now("UTC").strftime("%Y-%m-%d %H:%M UTC"),
            "mapEncoding": "uint16 little-endian base64, units mm, 65535 missing",
            "dailyEncoding": "uint16 little-endian base64, units tenths of mm",
        },
        "years": years,
        "points": {
            "lon": round_list(valid_lons, 2),
            "lat": round_list(valid_lats, 2),
        },
        "maps": {
            "annual": annual_maps,
            "jjas": jjas_maps,
            "climatologyAnnual": encode_uint16(clim_annual[valid_points]),
            "climatologyJjas": encode_uint16(clim_jjas[valid_points]),
        },
        "series": {
            "annual": annual_rows,
            "annualMean": round(float(np.nanmean(annual_series)), 1),
            "jjasMean": round(float(np.nanmean(jjas_series)), 1),
            "dailyMeanByYear": daily_by_year,
            "dailyLengths": daily_lengths,
            "monthlyByYear": monthly_by_year,
            "monthlyClimatology": monthly_clim,
            "wettestAllIndiaDays": wettest,
        },
    }

    (out_dir / "dashboard_data.js").write_text(
        "window.IMD_RAINFALL_DATA = "
        + json.dumps(payload, separators=(",", ":"))
        + ";\n",
        encoding="utf-8",
    )
    (out_dir / "annual_summary.csv").write_text(
        pd.DataFrame(annual_rows).to_csv(index=False, lineterminator="\n"),
        encoding="utf-8",
    )
    (out_dir / "monthly_by_year.csv").write_text(
        pd.DataFrame(monthly_by_year, index=range(1, 13)).rename_axis("month").to_csv(lineterminator="\n"),
        encoding="utf-8",
    )
    (out_dir / "manifest.json").write_text(
        json.dumps(payload["meta"], indent=2),
        encoding="utf-8",
    )

    boundary = json.loads(boundary_path.read_text(encoding="utf-8"))
    boundary_js = "window.INDIA_BOUNDARY = " + json.dumps(boundary, separators=(",", ":")) + ";\n"
    boundary_path.with_suffix(".js").write_text(boundary_js, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", type=Path, default=Path("outputs/imd-rainfall-dashboard/raw"))
    parser.add_argument("--data-dir", type=Path, default=Path("outputs/imd-rainfall-dashboard/data"))
    parser.add_argument(
        "--boundary",
        type=Path,
        default=Path("outputs/imd-rainfall-dashboard/assets/india_adm0_simplified.geojson"),
    )
    args = parser.parse_args()

    files = sorted(args.raw_dir.glob("rainfall_*.grd"))
    if not files:
        raise SystemExit(f"No rainfall_*.grd files found in {args.raw_dir}")

    lats = LAT0 + np.arange(NLAT) * DX
    weights = np.cos(np.deg2rad(lats)).reshape(1, NLAT, 1).astype(np.float32)
    products: list[YearProduct] = []
    for path in files:
        year = int(path.stem.split("_")[-1])
        print(f"processing {year}")
        products.append(process_year(path, year, weights))

    build_data(products, args.boundary, args.data_dir)
    print(f"wrote dashboard data for {len(products)} years to {args.data_dir}")


if __name__ == "__main__":
    main()
