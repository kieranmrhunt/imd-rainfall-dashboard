"""Build the dashboard's compact recent-rainfall payload.

The script downloads official IMD real-time 0.25 degree daily grids, matches
them to exact 1991-2020 calendar-day normals from the bundled historical map
assets, and writes a browser-ready JavaScript payload.
"""

from __future__ import annotations

import argparse
import base64
import calendar
import gzip
import hashlib
import json
import math
import os
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import numpy as np


NLON = 135
NLAT = 129
LON0 = 66.5
LAT0 = 6.5
DX = 0.25
GRID_VALUE_COUNT = NLON * NLAT
GRID_BYTE_COUNT = GRID_VALUE_COUNT * 4
BASELINE_START = 1991
BASELINE_END = 2020
UINT16_MISSING = 65535
MAP_SCALE = 10.0
REALTIME_PAGE = "https://imdpune.gov.in/cmpg/Realtimedata/Rainfall/Rain_Download.html"
REALTIME_ENDPOINT = "https://imdpune.gov.in/cmpg/Realtimedata/Rainfall/rain.php"
BASELINE_URL = (
    "https://raw.githubusercontent.com/kieranmrhunt/"
    "imd-rainfall-dashboard/main/data/daily_maps/rainfall_{year}.u16.gz"
)
USER_AGENT = "IMD rainfall dashboard updater/1.0 (+https://kieranmrhunt.github.io/imd-rainfall-dashboard/)"


@dataclass(frozen=True)
class Region:
    id: str
    name: str
    mask: np.ndarray


@dataclass(frozen=True)
class Window:
    id: str
    label: str
    short_label: str
    start: date
    end: date
    status: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dashboard-data",
        type=Path,
        default=Path("data/dashboard_data.js"),
        help="Existing dashboard data JavaScript, used for point order and region metadata.",
    )
    parser.add_argument(
        "--state-boundary",
        type=Path,
        default=Path("assets/india_adm1_simplified.geojson"),
        help="Simplified India ADM1 GeoJSON.",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("runtime/realtime"),
        help="Directory for cached IMD real-time binary grids.",
    )
    parser.add_argument(
        "--climatology-dir",
        type=Path,
        default=Path("runtime/climatology"),
        help="Directory for baseline map assets and the derived calendar-day cache.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/recent_data.js"),
        help="Browser-ready output JavaScript.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/recent_manifest.json"),
        help="Small machine-readable update manifest.",
    )
    parser.add_argument(
        "--today",
        type=date.fromisoformat,
        default=datetime.now(timezone.utc).date(),
        help="Date to probe backwards from (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--probe-days",
        type=int,
        default=10,
        help="Maximum number of dates to probe when finding the latest available grid.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Network timeout in seconds.",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Network attempts per file.",
    )
    parser.add_argument(
        "--request-pause",
        type=float,
        default=0.08,
        help="Polite pause between uncached IMD requests.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Use cached files only; do not make network requests.",
    )
    return parser.parse_args()


def load_assigned_json(path: Path, prefix: str) -> dict:
    text = path.read_text(encoding="utf-8").strip()
    if not text.startswith(prefix):
        raise ValueError(f"{path}: expected assignment beginning {prefix!r}")
    payload = text[len(prefix) :].strip()
    if payload.endswith(";"):
        payload = payload[:-1]
    return json.loads(payload)


def ascii_label(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return normalized.encode("ascii", "ignore").decode("ascii")


def slug(value: str) -> str:
    label = ascii_label(value).lower()
    chars = [character if character.isalnum() else "-" for character in label]
    return "-".join("".join(chars).split("-"))


def flatten_coords(coords: list) -> list[tuple[float, float]]:
    if not coords:
        return []
    if isinstance(coords[0], (int, float)):
        return [(float(coords[0]), float(coords[1]))]
    result: list[tuple[float, float]] = []
    for child in coords:
        result.extend(flatten_coords(child))
    return result


def point_in_ring(lon: float, lat: float, ring: list) -> bool:
    inside = False
    if len(ring) < 4:
        return False
    x1, y1 = ring[-1][0], ring[-1][1]
    for point in ring:
        x2, y2 = point[0], point[1]
        if ((y1 > lat) != (y2 > lat)) and (
            lon < (x2 - x1) * (lat - y1) / ((y2 - y1) or 1e-12) + x1
        ):
            inside = not inside
        x1, y1 = x2, y2
    return inside


def point_in_polygon(lon: float, lat: float, polygon: list) -> bool:
    if not polygon or not point_in_ring(lon, lat, polygon[0]):
        return False
    return not any(point_in_ring(lon, lat, hole) for hole in polygon[1:])


def point_in_geometry(lon: float, lat: float, geometry: dict) -> bool:
    if geometry["type"] == "Polygon":
        return point_in_polygon(lon, lat, geometry["coordinates"])
    if geometry["type"] == "MultiPolygon":
        return any(point_in_polygon(lon, lat, polygon) for polygon in geometry["coordinates"])
    return False


def point_indices(data: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    lons = np.asarray(data["points"]["lon"], dtype=np.float64)
    lats = np.asarray(data["points"]["lat"], dtype=np.float64)
    if lons.shape != lats.shape or not lons.size:
        raise ValueError("Dashboard point coordinates are empty or inconsistent")
    cols = np.rint((lons - LON0) / DX).astype(np.int64)
    rows = np.rint((lats - LAT0) / DX).astype(np.int64)
    if (
        np.any(cols < 0)
        or np.any(cols >= NLON)
        or np.any(rows < 0)
        or np.any(rows >= NLAT)
    ):
        raise ValueError("Dashboard point coordinates extend outside the IMD grid")
    reconstructed_lons = LON0 + cols * DX
    reconstructed_lats = LAT0 + rows * DX
    if not np.allclose(lons, reconstructed_lons) or not np.allclose(lats, reconstructed_lats):
        raise ValueError("Dashboard points do not lie on the expected 0.25 degree grid")
    return lons, lats, rows * NLON + cols


def build_regions(data: dict, boundary_path: Path, lons: np.ndarray, lats: np.ndarray) -> list[Region]:
    expected = data.get("regions", {}).get("list", [])
    if not expected:
        return [Region("all-india", "All India", np.ones(lons.size, dtype=bool))]

    boundary = json.loads(boundary_path.read_text(encoding="utf-8"))
    features = {
        slug(feature.get("properties", {}).get("shapeName", "")): feature
        for feature in boundary.get("features", [])
    }
    regions = [Region("all-india", "All India", np.ones(lons.size, dtype=bool))]
    for item in expected:
        region_id = item["id"]
        if region_id == "all-india":
            continue
        feature = features.get(region_id)
        if not feature:
            raise ValueError(f"No ADM1 boundary found for dashboard region {region_id!r}")
        geometry = feature["geometry"]
        flat = flatten_coords(geometry["coordinates"])
        if not flat:
            raise ValueError(f"Boundary for {region_id!r} has no coordinates")
        min_lon = min(point[0] for point in flat) - DX
        max_lon = max(point[0] for point in flat) + DX
        min_lat = min(point[1] for point in flat) - DX
        max_lat = max(point[1] for point in flat) + DX
        candidates = (
            (lons >= min_lon)
            & (lons <= max_lon)
            & (lats >= min_lat)
            & (lats <= max_lat)
        )
        mask = np.zeros(lons.size, dtype=bool)
        for point_index in np.flatnonzero(candidates):
            mask[point_index] = point_in_geometry(
                float(lons[point_index]),
                float(lats[point_index]),
                geometry,
            )
        expected_count = int(item.get("cellCount", 0))
        if expected_count and int(mask.sum()) != expected_count:
            raise ValueError(
                f"{region_id}: rebuilt {int(mask.sum())} cells, expected {expected_count}"
            )
        regions.append(Region(region_id, item["name"], mask))
    return regions


def request_bytes(
    url: str,
    *,
    timeout: float,
    retries: int,
    data: bytes | None = None,
) -> bytes:
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/octet-stream,*/*;q=0.8",
            "Referer": REALTIME_PAGE,
        },
        method="POST" if data is not None else "GET",
    )
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last_error = error
            if attempt < retries:
                time.sleep(min(8.0, 1.5**attempt))
    raise RuntimeError(f"Could not download {url}: {last_error}") from last_error


def validate_daily_bytes(raw: bytes, target: date) -> np.ndarray:
    if len(raw) != GRID_BYTE_COUNT:
        raise ValueError(
            f"{target}: expected {GRID_BYTE_COUNT:,} bytes, received {len(raw):,}"
        )
    grid = np.frombuffer(raw, dtype="<f4")
    plausible = np.isfinite(grid) & (grid > -900.0) & (grid < 10000.0)
    if int(plausible.sum()) < 4_500:
        raise ValueError(f"{target}: only {int(plausible.sum()):,} plausible grid cells")
    nonnegative = grid[plausible & (grid >= 0)]
    if not nonnegative.size:
        raise ValueError(f"{target}: grid contains no non-negative rainfall")
    return grid


def daily_cache_path(cache_dir: Path, target: date) -> Path:
    return cache_dir / f"rainfall_{target:%Y%m%d}.grd"


def load_or_download_day(
    target: date,
    *,
    cache_dir: Path,
    timeout: float,
    retries: int,
    offline: bool,
    request_pause: float,
) -> np.ndarray | None:
    path = daily_cache_path(cache_dir, target)
    if path.exists():
        try:
            return validate_daily_bytes(path.read_bytes(), target)
        except ValueError:
            path.unlink()
    if offline:
        return None
    body = urllib.parse.urlencode({"rain": target.strftime("%d%m%Y")}).encode("ascii")
    try:
        raw = request_bytes(
            REALTIME_ENDPOINT,
            timeout=timeout,
            retries=retries,
            data=body,
        )
        grid = validate_daily_bytes(raw, target)
    except (RuntimeError, ValueError) as error:
        print(f"unavailable {target}: {error}")
        return None
    cache_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(path, raw)
    if request_pause > 0:
        time.sleep(request_pause)
    print(f"downloaded {target} ({len(raw):,} bytes)")
    return grid


def find_latest(
    start: date,
    *,
    probe_days: int,
    cache_dir: Path,
    timeout: float,
    retries: int,
    offline: bool,
    request_pause: float,
) -> tuple[date, np.ndarray]:
    for offset in range(max(1, probe_days)):
        target = start - timedelta(days=offset)
        grid = load_or_download_day(
            target,
            cache_dir=cache_dir,
            timeout=timeout,
            retries=retries,
            offline=offline,
            request_pause=request_pause,
        )
        if grid is not None:
            return target, grid
    raise RuntimeError(
        f"No valid IMD daily grid found from {start - timedelta(days=probe_days - 1)} "
        f"through {start}"
    )


def days_in_year(year: int) -> int:
    return 366 if calendar.isleap(year) else 365


def load_or_download_baseline(
    year: int,
    *,
    directory: Path,
    point_count: int,
    timeout: float,
    retries: int,
    offline: bool,
) -> np.ndarray:
    path = directory / f"rainfall_{year}.u16.gz"
    if not path.exists():
        if offline:
            raise FileNotFoundError(f"Missing baseline asset {path}")
        raw = request_bytes(
            BASELINE_URL.format(year=year),
            timeout=timeout,
            retries=retries,
        )
        try:
            decompressed = gzip.decompress(raw)
        except gzip.BadGzipFile as error:
            raise ValueError(f"{year}: downloaded baseline asset is not gzip") from error
        expected_bytes = days_in_year(year) * point_count * 2
        if len(decompressed) != expected_bytes:
            raise ValueError(
                f"{year}: expected {expected_bytes:,} baseline bytes, got {len(decompressed):,}"
            )
        directory.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(path, raw)
        print(f"downloaded baseline map {year}")
    raw = gzip.decompress(path.read_bytes())
    values = np.frombuffer(raw, dtype="<u2")
    expected = days_in_year(year) * point_count
    if values.size != expected:
        raise ValueError(f"{path}: expected {expected:,} values, got {values.size:,}")
    return values.reshape((days_in_year(year), point_count))


def calendar_key(target: date) -> str:
    return target.strftime("%m-%d")


def build_calendar_normals(
    *,
    directory: Path,
    point_count: int,
    timeout: float,
    retries: int,
    offline: bool,
) -> tuple[list[str], np.ndarray, np.ndarray]:
    cache_path = directory / "calendar_day_normals_1991_2020.npz"
    if cache_path.exists():
        with np.load(cache_path, allow_pickle=False) as cached:
            keys = [str(value) for value in cached["keys"].tolist()]
            normals = cached["normals"].astype(np.float32)
            counts = cached["counts"].astype(np.uint8)
        if normals.shape == (len(keys), point_count) and counts.shape == normals.shape:
            return keys, normals, counts
        cache_path.unlink()

    keys = [
        f"{month:02d}-{day:02d}"
        for month in range(1, 13)
        for day in range(1, calendar.monthrange(2000, month)[1] + 1)
    ]
    key_index = {key: index for index, key in enumerate(keys)}
    sums = np.zeros((len(keys), point_count), dtype=np.float64)
    counts = np.zeros((len(keys), point_count), dtype=np.uint8)
    for year in range(BASELINE_START, BASELINE_END + 1):
        values = load_or_download_baseline(
            year,
            directory=directory,
            point_count=point_count,
            timeout=timeout,
            retries=retries,
            offline=offline,
        )
        start = date(year, 1, 1)
        for day_index in range(values.shape[0]):
            target = start + timedelta(days=day_index)
            row = values[day_index]
            valid = row != UINT16_MISSING
            index = key_index[calendar_key(target)]
            sums[index, valid] += row[valid] / MAP_SCALE
            counts[index, valid] += 1
    normals = np.full(sums.shape, np.nan, dtype=np.float32)
    np.divide(sums, counts, out=normals, where=counts > 0)
    directory.mkdir(parents=True, exist_ok=True)
    temp = cache_path.with_name(f".{cache_path.name}.{os.getpid()}.tmp.npz")
    np.savez_compressed(temp, keys=np.asarray(keys), normals=normals, counts=counts)
    os.replace(temp, cache_path)
    print(f"built calendar-day climatology cache ({len(keys)} days)")
    return keys, normals, counts


def date_range(start: date, end: date) -> list[date]:
    if end < start:
        return []
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def previous_month_start(target: date) -> date:
    return (target.replace(day=1) - timedelta(days=1)).replace(day=1)


def windows_for(latest: date) -> list[Window]:
    current_start = latest.replace(day=1)
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end.replace(day=1)
    return [
        Window(
            "last-7-days",
            "Last 7 days",
            "Last 7",
            latest - timedelta(days=6),
            latest,
            "complete",
        ),
        Window(
            "previous-7-days",
            "Previous 7 days",
            "Previous 7",
            latest - timedelta(days=13),
            latest - timedelta(days=7),
            "complete",
        ),
        Window(
            "current-month",
            f"{latest:%B} to date",
            "Current month",
            current_start,
            latest,
            "partial",
        ),
        Window(
            "last-complete-month",
            f"{previous_start:%B %Y}",
            "Last full month",
            previous_start,
            previous_end,
            "complete",
        ),
    ]


def subset_grid(grid: np.ndarray, flat_indices: np.ndarray) -> np.ndarray:
    values = np.asarray(grid[flat_indices], dtype=np.float32)
    invalid = ~np.isfinite(values) | (values < 0.0) | (values > 10000.0)
    values[invalid] = np.nan
    return values


def encode_u16(values: np.ndarray, scale: float = MAP_SCALE) -> str:
    array = np.asarray(values, dtype=np.float64)
    encoded = np.full(array.shape, UINT16_MISSING, dtype="<u2")
    valid = np.isfinite(array)
    if np.any(array[valid] < 0):
        raise ValueError("Unsigned rainfall encoding received a negative value")
    maximum = (UINT16_MISSING - 1) / scale
    if np.any(array[valid] > maximum):
        peak = float(np.nanmax(array))
        raise ValueError(f"Rainfall map peak {peak:.1f} mm exceeds uint16 encoding range")
    encoded[valid] = np.rint(array[valid] * scale).astype(np.uint16)
    return base64.b64encode(encoded.tobytes()).decode("ascii")


def weighted_mean(values: np.ndarray, mask: np.ndarray, weights: np.ndarray) -> float:
    valid = mask & np.isfinite(values)
    if not np.any(valid):
        return math.nan
    return float(np.average(values[valid], weights=weights[valid]))


def rounded(value: float, digits: int = 1) -> float | None:
    if not math.isfinite(value):
        return None
    return round(value, digits)


def region_daily_summary(
    values: np.ndarray,
    regions: Iterable[Region],
    weights: np.ndarray,
) -> dict[str, dict]:
    return {
        region.id: {"observed": rounded(weighted_mean(values, region.mask, weights))}
        for region in regions
    }


def region_period_summary(
    observed: np.ndarray,
    normal: np.ndarray,
    regions: Iterable[Region],
    weights: np.ndarray,
) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for region in regions:
        observed_mean = weighted_mean(observed, region.mask, weights)
        normal_mean = weighted_mean(normal, region.mask, weights)
        anomaly = observed_mean - normal_mean
        percent_anomaly = 100.0 * anomaly / normal_mean if normal_mean > 0 else math.nan
        percent_normal = 100.0 * observed_mean / normal_mean if normal_mean > 0 else math.nan
        result[region.id] = {
            "observed": rounded(observed_mean),
            "normal": rounded(normal_mean),
            "anomaly": rounded(anomaly),
            "percentAnomaly": rounded(percent_anomaly),
            "percentNormal": rounded(percent_normal),
        }
    return result


def atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def atomic_write_text(path: Path, content: str) -> None:
    atomic_write_bytes(path, content.encode("utf-8"))


def build_payload(args: argparse.Namespace) -> tuple[dict, dict]:
    data = load_assigned_json(args.dashboard_data, "window.IMD_RAINFALL_DATA =")
    lons, lats, flat_indices = point_indices(data)
    point_count = lons.size
    regions = build_regions(data, args.state_boundary, lons, lats)
    weights = np.cos(np.deg2rad(lats))

    latest, latest_grid = find_latest(
        args.today,
        probe_days=args.probe_days,
        cache_dir=args.cache_dir,
        timeout=args.timeout,
        retries=args.retries,
        offline=args.offline,
        request_pause=args.request_pause,
    )
    windows = windows_for(latest)
    required_dates = set()
    for window in windows:
        required_dates.update(date_range(window.start, window.end))
    required_dates.update(date_range(latest - timedelta(days=31), latest))

    full_grids: dict[date, np.ndarray] = {latest: latest_grid}
    for target in sorted(required_dates):
        if target == latest:
            continue
        grid = load_or_download_day(
            target,
            cache_dir=args.cache_dir,
            timeout=args.timeout,
            retries=args.retries,
            offline=args.offline,
            request_pause=args.request_pause,
        )
        if grid is not None:
            full_grids[target] = grid

    available = sorted(full_grids)
    recent_dates = [target for target in available if target <= latest][-10:]
    if len(recent_dates) < 10:
        raise RuntimeError(
            f"Only {len(recent_dates)} valid daily grids are available; at least 10 are required"
        )

    values_by_date = {
        target: subset_grid(grid, flat_indices)
        for target, grid in full_grids.items()
    }
    calendar_keys, calendar_normals, calendar_counts = build_calendar_normals(
        directory=args.climatology_dir,
        point_count=point_count,
        timeout=args.timeout,
        retries=args.retries,
        offline=args.offline,
    )
    normal_index = {key: index for index, key in enumerate(calendar_keys)}

    daily_payload = []
    for target in reversed(recent_dates):
        values = values_by_date[target]
        daily_payload.append(
            {
                "id": f"daily-{target.isoformat()}",
                "date": target.isoformat(),
                "label": target.strftime("%-d %b") if os.name != "nt" else target.strftime("%d %b").lstrip("0"),
                "observed": encode_u16(values),
                "regions": region_daily_summary(values, regions, weights),
            }
        )

    period_payload = []
    missing_dates: set[date] = set()
    for window in windows:
        expected_dates = date_range(window.start, window.end)
        used_dates = [target for target in expected_dates if target in values_by_date]
        missing = [target for target in expected_dates if target not in values_by_date]
        missing_dates.update(missing)
        if not used_dates:
            continue

        observed_stack = np.stack([values_by_date[target] for target in used_dates])
        observed = np.sum(observed_stack, axis=0)
        observed[np.any(~np.isfinite(observed_stack), axis=0)] = np.nan

        normal_stack = np.stack(
            [calendar_normals[normal_index[calendar_key(target)]] for target in used_dates]
        )
        normal = np.sum(normal_stack, axis=0)
        normal[np.any(~np.isfinite(normal_stack), axis=0)] = np.nan
        baseline_samples = min(
            int(np.nanmin(calendar_counts[normal_index[calendar_key(target)]]))
            for target in used_dates
        )
        status = window.status if not missing else "incomplete"
        period_payload.append(
            {
                "id": window.id,
                "label": window.label,
                "shortLabel": window.short_label,
                "startDate": window.start.isoformat(),
                "endDate": window.end.isoformat(),
                "expectedDays": len(expected_dates),
                "availableDays": len(used_dates),
                "missingDates": [target.isoformat() for target in missing],
                "status": status,
                "baselineSamplesPerCalendarDay": baseline_samples,
                "observed": encode_u16(observed),
                "normal": encode_u16(normal),
                "regions": region_period_summary(observed, normal, regions, weights),
            }
        )

    generated = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    payload = {
        "meta": {
            "title": "Recent IMD Daily Gridded Rainfall",
            "source": "India Meteorological Department real-time 0.25 x 0.25 degree daily rainfall",
            "sourcePage": REALTIME_PAGE,
            "baseline": f"{BASELINE_START}-{BASELINE_END}",
            "generated": generated,
            "latestAvailableDate": latest.isoformat(),
            "coverageStart": min(required_dates).isoformat(),
            "coverageEnd": latest.isoformat(),
            "pointCount": int(point_count),
            "regionCount": len(regions),
            "encoding": "uint16 little-endian base64; tenths of mm; 65535 missing",
            "mapScale": MAP_SCALE,
            "normalMethod": (
                "Observed accumulations minus the mean accumulation for matching "
                "calendar dates in 1991-2020"
            ),
        },
        "daily": daily_payload,
        "periods": period_payload,
        "availability": {
            "requestedStart": min(required_dates).isoformat(),
            "requestedEnd": latest.isoformat(),
            "availableDays": len([target for target in required_dates if target in full_grids]),
            "expectedDays": len(required_dates),
            "missingDates": [target.isoformat() for target in sorted(missing_dates)],
        },
    }
    stable_payload = json.loads(json.dumps(payload))
    stable_payload["meta"].pop("generated", None)
    compact = json.dumps(stable_payload, ensure_ascii=True, separators=(",", ":"))
    digest = hashlib.sha256(compact.encode("utf-8")).hexdigest()
    manifest = {
        "schemaVersion": 1,
        "generated": generated,
        "latestAvailableDate": latest.isoformat(),
        "baseline": f"{BASELINE_START}-{BASELINE_END}",
        "dailyDates": [item["date"] for item in daily_payload],
        "periods": [
            {
                key: item[key]
                for key in (
                    "id",
                    "label",
                    "startDate",
                    "endDate",
                    "expectedDays",
                    "availableDays",
                    "status",
                )
            }
            for item in period_payload
        ],
        "missingDates": payload["availability"]["missingDates"],
        "payloadSha256": digest,
    }
    return payload, manifest


def main() -> None:
    args = parse_args()
    payload, manifest = build_payload(args)
    if args.output.exists() and args.manifest.exists():
        try:
            previous_manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            previous_manifest = {}
        if previous_manifest.get("payloadSha256") == manifest["payloadSha256"]:
            print(
                f"no data changes; {args.output} already covers "
                f"{manifest['latestAvailableDate']}"
            )
            return
    javascript = (
        "window.IMD_RECENT_RAINFALL = "
        + json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
        + ";\n"
    )
    atomic_write_text(args.output, javascript)
    atomic_write_text(args.manifest, json.dumps(manifest, indent=2) + "\n")
    latest = payload["meta"]["latestAvailableDate"]
    missing = len(payload["availability"]["missingDates"])
    print(
        f"wrote {args.output} ({len(javascript):,} bytes); "
        f"latest {latest}; {missing} missing requested dates"
    )


if __name__ == "__main__":
    main()
