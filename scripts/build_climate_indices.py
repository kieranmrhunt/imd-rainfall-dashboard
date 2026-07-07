"""Build compact climate-index payload for the IMD rainfall dashboard."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


MISSING_LIMIT = -90.0
MONTHS = range(1, 13)


def parse_phase_file(path: Path) -> dict[str, list[float | int]]:
    records: dict[str, list[float | int]] = {}
    with path.open("r", encoding="utf-8") as handle:
        next(handle, None)
        for line in handle:
            parts = line.split()
            if len(parts) < 8:
                continue
            year, month, day = map(int, parts[:3])
            if year < 1901 or year > 2025:
                continue
            phase = int(parts[5])
            amp = float(parts[6])
            if phase < 1 or phase > 8:
                continue
            records[f"{year:04d}-{month:02d}-{day:02d}"] = [phase, round(amp, 3)]
    return records


def merge_phase_files(*paths: Path) -> dict[str, list[float | int]]:
    records: dict[str, list[float | int]] = {}
    for path in paths:
        records.update(parse_phase_file(path))
    return dict(sorted(records.items()))


def parse_monthly_matrix(path: Path) -> dict[str, float]:
    records: dict[str, float] = {}
    with path.open("r", encoding="utf-8") as handle:
        next(handle, None)
        for line in handle:
            parts = line.split()
            if len(parts) < 13:
                continue
            try:
                year = int(parts[0])
            except ValueError:
                continue
            if year < 1901 or year > 2025:
                continue
            for month, raw in zip(MONTHS, parts[1:13]):
                value = float(raw)
                if value <= MISSING_LIMIT:
                    continue
                records[f"{year:04d}-{month:02d}"] = round(value, 3)
    return records


def build_payload(indices_dir: Path) -> dict:
    return {
        "meta": {
            "title": "Climate-mode indices for IMD rainfall composites",
            "dailyPhaseEncoding": "date -> [phase, normalized amplitude]",
            "monthlyEncoding": "YYYY-MM -> anomaly/index value",
            "sources": {
                "mjo": "IPRC MJO 25-90 day filtered PC extension",
                "bsiso": "IPRC BSISO 25-90 day filtered PC extension",
                "nino34": "NOAA PSL Nino 3.4 monthly SST anomaly",
                "oni": "NOAA PSL Oceanic Nino Index",
                "dmi": "NOAA PSL HadISST Dipole Mode Index",
                "nao": "NOAA PSL monthly NAO index",
            },
        },
        "daily": {
            "mjo": merge_phase_files(
                indices_dir / "MJO_25-90bpfil_pc.extension.txt",
                indices_dir / "MJO_25-90bpfil.rt_pc.txt",
            ),
            "bsiso": merge_phase_files(
                indices_dir / "BSISO_25-90bpfil_pc.extension.txt",
                indices_dir / "BSISO_25-90bpfil.rt_pc.txt",
            ),
        },
        "monthly": {
            "nino34": parse_monthly_matrix(indices_dir / "nina34.anom.data"),
            "oni": parse_monthly_matrix(indices_dir / "oni.data"),
            "dmi": parse_monthly_matrix(indices_dir / "dmi.had.long.data"),
            "nao": parse_monthly_matrix(indices_dir / "nao.data"),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--indices-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    payload = build_payload(args.indices_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "window.IMD_CLIMATE_INDICES = "
        + json.dumps(payload, separators=(",", ":"))
        + ";\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
