"""Download IMD 0.25 degree daily gridded rainfall binary files.

Source page:
https://www.imdpune.gov.in/cmpg/Griddata/Rainfall_25_Bin.html

The form posts a selected year as the field `rain` to `rainfall.php`.
Files are classic IMD binary grids: daily records, 135 longitude points,
129 latitude points, float32 little-endian rainfall in mm, with -999
outside the analysed India domain.
"""

from __future__ import annotations

import argparse
import calendar
import time
import urllib.parse
import urllib.request
from pathlib import Path


ENDPOINT = "https://www.imdpune.gov.in/cmpg/Griddata/rainfall.php"
GRID_NLON = 135
GRID_NLAT = 129
BYTES_PER_VALUE = 4


def expected_bytes(year: int) -> int:
    days = 366 if calendar.isleap(year) else 365
    return days * GRID_NLON * GRID_NLAT * BYTES_PER_VALUE


def is_valid_file(path: Path, year: int) -> bool:
    return path.exists() and path.stat().st_size == expected_bytes(year)


def download_year(year: int, out_dir: Path, retries: int, pause: float) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"rainfall_{year}.grd"
    tmp_file = out_dir / f"rainfall_{year}.grd.part"

    if is_valid_file(out_file, year):
        print(f"{year}: exists ({out_file.stat().st_size / 1_000_000:.1f} MB)", flush=True)
        return

    if tmp_file.exists():
        tmp_file.unlink()

    body = urllib.parse.urlencode({"rain": str(year)}).encode("ascii")
    headers = {
        "User-Agent": "codex-imd-rainfall-dashboard/1.0",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    request = urllib.request.Request(ENDPOINT, data=body, headers=headers, method="POST")
    expected = expected_bytes(year)

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            start = time.time()
            with urllib.request.urlopen(request, timeout=180) as response, tmp_file.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)

            size = tmp_file.stat().st_size
            if size != expected:
                raise RuntimeError(f"expected {expected} bytes, got {size} bytes")

            tmp_file.replace(out_file)
            elapsed = max(time.time() - start, 0.001)
            print(f"{year}: downloaded {size / 1_000_000:.1f} MB in {elapsed:.1f}s", flush=True)
            time.sleep(pause)
            return
        except Exception as exc:  # noqa: BLE001 - keep retry diagnostics simple.
            last_error = exc
            print(f"{year}: attempt {attempt}/{retries} failed: {exc}", flush=True)
            if tmp_file.exists():
                tmp_file.unlink()
            time.sleep(max(pause, 2.0) * attempt)

    raise RuntimeError(f"{year}: failed after {retries} attempts") from last_error


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=1901)
    parser.add_argument("--end", type=int, default=2025)
    parser.add_argument("--out-dir", type=Path, default=Path("outputs/imd-rainfall-dashboard/raw"))
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--pause", type=float, default=1.5)
    args = parser.parse_args()

    for year in range(args.start, args.end + 1):
        download_year(year, args.out_dir, args.retries, args.pause)


if __name__ == "__main__":
    main()
