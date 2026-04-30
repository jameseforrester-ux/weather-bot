"""Airport database & city geocoding.

- Pulls the OurAirports global dataset (~80k airports) on first run.
- Provides ICAO/IATA lookup, nearest-airport search, and city geocoding
  via Open-Meteo's free geocoding API.
"""
import csv
import math
import os
from dataclasses import dataclass
from typing import List, Optional, Iterable

import httpx

OURAIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"

# Increase CSV field size limit just in case
csv.field_size_limit(10 ** 7)


@dataclass
class Airport:
    icao: str
    iata: str
    name: str
    city: str
    region: str
    country: str
    lat: float
    lon: float
    type: str
    elevation_ft: Optional[float] = None

    @property
    def display_code(self) -> str:
        return self.iata or self.icao

    @property
    def type_emoji(self) -> str:
        return {
            "large_airport": "🛬",
            "medium_airport": "✈️",
            "small_airport": "🛩️",
        }.get(self.type, "✈️")


class AirportDatabase:
    """In-memory global airport database."""

    def __init__(self) -> None:
        self.airports: List[Airport] = []
        self.icao_index: dict = {}
        self.iata_index: dict = {}

    def load_from_csv(self, path: str) -> None:
        with open(path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                t = row.get("type", "")
                # Skip closed/heliport/seaplane to keep things focused on real
                # airports the user would actually fly to/from. Includes all
                # weather-station-equipped airports.
                if t not in ("large_airport", "medium_airport", "small_airport"):
                    continue
                try:
                    lat = float(row["latitude_deg"])
                    lon = float(row["longitude_deg"])
                except (TypeError, ValueError, KeyError):
                    continue

                icao = (row.get("ident") or "").upper().strip()
                iata = (row.get("iata_code") or "").upper().strip()
                if not icao:
                    continue

                elev = row.get("elevation_ft") or None
                try:
                    elev = float(elev) if elev not in (None, "") else None
                except (TypeError, ValueError):
                    elev = None

                a = Airport(
                    icao=icao,
                    iata=iata,
                    name=row.get("name", "").strip(),
                    city=(row.get("municipality") or "").strip(),
                    region=(row.get("iso_region") or "").strip(),
                    country=(row.get("iso_country") or "").strip(),
                    lat=lat,
                    lon=lon,
                    type=t,
                    elevation_ft=elev,
                )
                self.airports.append(a)
                self.icao_index[icao] = a
                if iata:
                    # Don't override an existing IATA mapping for a larger airport
                    existing = self.iata_index.get(iata)
                    if (existing is None
                            or _type_priority(a.type) < _type_priority(existing.type)):
                        self.iata_index[iata] = a

    def lookup(self, code: str) -> Optional[Airport]:
        if not code:
            return None
        c = code.upper().strip()
        return self.icao_index.get(c) or self.iata_index.get(c)

    def search_near(
        self,
        lat: float,
        lon: float,
        radius_km: float = 200.0,
        limit: int = 8,
    ) -> List[Airport]:
        """Return airports within radius_km, ordered by (size_priority, distance)."""
        candidates = []
        for a in self.airports:
            d = haversine_km(lat, lon, a.lat, a.lon)
            if d <= radius_km:
                candidates.append((d, a))
        candidates.sort(
            key=lambda x: (_type_priority(x[1].type), x[0])
        )
        return [a for _, a in candidates[:limit]]


def _type_priority(t: str) -> int:
    return {"large_airport": 0, "medium_airport": 1, "small_airport": 2}.get(t, 3)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


async def geocode_city(name: str, count: int = 5) -> List[dict]:
    """Geocode a city name using Open-Meteo's free geocoding API."""
    params = {"name": name, "count": count, "language": "en", "format": "json"}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(GEOCODE_URL, params=params)
        r.raise_for_status()
        data = r.json()
    return data.get("results") or []


def ensure_airport_data(path: str = "data/airports.csv") -> None:
    """Download the OurAirports CSV if missing or implausibly small."""
    needs_dl = (not os.path.exists(path)) or os.path.getsize(path) < 1_000_000
    if not needs_dl:
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    print(f"[airports] Downloading global airport database from {OURAIRPORTS_URL} ...")
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        with client.stream("GET", OURAIRPORTS_URL) as resp:
            resp.raise_for_status()
            with open(path, "wb") as f:
                for chunk in resp.iter_bytes(8192):
                    f.write(chunk)
    print(f"[airports] Downloaded {os.path.getsize(path) / 1_000_000:.1f} MB")
