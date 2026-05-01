"""Polymarket integration: 33 daily highest-temperature city markets.

Each city has:
  - a Polymarket URL slug (e.g. 'nyc', 'los-angeles')
  - a unit (°F or °C — auto-detected per-city via market questions too)
  - the resolution station Polymarket actually settles on
  - a list of explicit ICAOs we map directly to this city
  - city center lat/lon for the geographic fallback
  - an IANA timezone for city-local "today"

Lookup precedence for an arbitrary airport ICAO:
  1. Explicit map  (e.g. KBKF → denver)
  2. Geographic fallback within 80 km of the city center
  3. None  → bot hides Polymarket section
"""
from __future__ import annotations

import calendar
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import httpx

log = logging.getLogger("polymarket")

GAMMA_BASE = "https://gamma-api.polymarket.com"
EVENT_BY_SLUG = GAMMA_BASE + "/events/slug/{slug}"
SITE_BASE = "https://polymarket.com"

# Geographic fallback: how far is "still in this city's metro"
GEO_FALLBACK_RADIUS_KM = 80.0


@dataclass
class CityConfig:
    slug: str
    display: str
    unit: str                       # 'C' or 'F'
    timezone: str                   # IANA tz, e.g. 'America/New_York'
    resolves_at_icao: str
    resolves_at_name: str
    resolves_at_lat: float
    resolves_at_lon: float
    center_lat: float
    center_lon: float
    explicit_airports: List[str] = field(default_factory=list)


# 33 supported cities. Resolution stations confirmed against live markets
# where verified; remainder use the obvious primary airport for the city.
SUPPORTED_CITIES: Dict[str, CityConfig] = {
    # ── North America ──
    "nyc": CityConfig(
        slug="nyc", display="NYC", unit="F", timezone="America/New_York",
        resolves_at_icao="KLGA", resolves_at_name="LaGuardia",
        resolves_at_lat=40.7773, resolves_at_lon=-73.8726,
        center_lat=40.7128, center_lon=-74.0060,
        explicit_airports=["KJFK", "KLGA", "KEWR", "KHPN", "KISP", "KSWF",
                           "KFRG", "KTEB"]),
    "los-angeles": CityConfig(
        slug="los-angeles", display="Los Angeles", unit="F",
        timezone="America/Los_Angeles",
        resolves_at_icao="KLAX", resolves_at_name="LAX",
        resolves_at_lat=33.9425, resolves_at_lon=-118.4081,
        center_lat=34.0522, center_lon=-118.2437,
        explicit_airports=["KLAX", "KBUR", "KLGB", "KSNA", "KVNY", "KHHR",
                           "KCNO", "KFUL", "KONT", "KWHP"]),
    "chicago": CityConfig(
        slug="chicago", display="Chicago", unit="F",
        timezone="America/Chicago",
        resolves_at_icao="KORD", resolves_at_name="O'Hare",
        resolves_at_lat=41.9742, resolves_at_lon=-87.9073,
        center_lat=41.8781, center_lon=-87.6298,
        explicit_airports=["KORD", "KMDW", "KPWK", "KDPA", "KGYY", "KARR"]),
    "miami": CityConfig(
        slug="miami", display="Miami", unit="F",
        timezone="America/New_York",
        resolves_at_icao="KMIA", resolves_at_name="Miami Intl",
        resolves_at_lat=25.7959, resolves_at_lon=-80.2870,
        center_lat=25.7617, center_lon=-80.1918,
        explicit_airports=["KMIA", "KFLL", "KOPF", "KTMB", "KHWO", "KPBI"]),
    "atlanta": CityConfig(
        slug="atlanta", display="Atlanta", unit="F",
        timezone="America/New_York",
        resolves_at_icao="KATL", resolves_at_name="Hartsfield-Jackson",
        resolves_at_lat=33.6367, resolves_at_lon=-84.4281,
        center_lat=33.7490, center_lon=-84.3880,
        explicit_airports=["KATL", "KFTY", "KPDK", "KRYY", "KLZU", "KFFC"]),
    "denver": CityConfig(
        slug="denver", display="Denver", unit="F",
        timezone="America/Denver",
        resolves_at_icao="KBKF", resolves_at_name="Buckley Space Force Base",
        resolves_at_lat=39.7017, resolves_at_lon=-104.7517,
        center_lat=39.7392, center_lon=-104.9903,
        explicit_airports=["KDEN", "KAPA", "KBKF", "KBJC", "KFTG", "KEIK"]),
    "houston": CityConfig(
        slug="houston", display="Houston", unit="F",
        timezone="America/Chicago",
        resolves_at_icao="KHOU", resolves_at_name="William P. Hobby",
        resolves_at_lat=29.6454, resolves_at_lon=-95.2789,
        center_lat=29.7604, center_lon=-95.3698,
        explicit_airports=["KIAH", "KHOU", "KEFD", "KSGR", "KIWS", "KDWH"]),
    "seattle": CityConfig(
        slug="seattle", display="Seattle", unit="F",
        timezone="America/Los_Angeles",
        resolves_at_icao="KSEA", resolves_at_name="Sea-Tac",
        resolves_at_lat=47.4502, resolves_at_lon=-122.3088,
        center_lat=47.6062, center_lon=-122.3321,
        explicit_airports=["KSEA", "KBFI", "KRNT", "KPAE", "KTIW", "KOLM"]),
    "panama-city": CityConfig(
        slug="panama-city", display="Panama City", unit="F",
        timezone="America/Panama",
        resolves_at_icao="MPMG", resolves_at_name="Marcos A. Gelabert",
        resolves_at_lat=8.9733, resolves_at_lon=-79.5556,
        center_lat=8.9824, center_lon=-79.5199,
        explicit_airports=["MPMG", "MPTO"]),
    # ── South America ──
    "sao-paulo": CityConfig(
        slug="sao-paulo", display="São Paulo", unit="C",
        timezone="America/Sao_Paulo",
        resolves_at_icao="SBGR", resolves_at_name="Guarulhos Intl",
        resolves_at_lat=-23.4356, resolves_at_lon=-46.4731,
        center_lat=-23.5505, center_lon=-46.6333,
        explicit_airports=["SBGR", "SBSP", "SBKP", "SBMT"]),
    "buenos-aires": CityConfig(
        slug="buenos-aires", display="Buenos Aires", unit="C",
        timezone="America/Argentina/Buenos_Aires",
        resolves_at_icao="SAEZ", resolves_at_name="Ministro Pistarini (Ezeiza)",
        resolves_at_lat=-34.8222, resolves_at_lon=-58.5358,
        center_lat=-34.6037, center_lon=-58.3816,
        explicit_airports=["SAEZ", "SABE", "SADP"]),
    # ── Europe ──
    "london": CityConfig(
        slug="london", display="London", unit="C", timezone="Europe/London",
        resolves_at_icao="EGLC", resolves_at_name="London City Airport",
        resolves_at_lat=51.5053, resolves_at_lon=0.0553,
        center_lat=51.5074, center_lon=-0.1278,
        explicit_airports=["EGLL", "EGKK", "EGLC", "EGSS", "EGGW", "EGLF",
                           "EGTK", "EGTC", "EGMC", "EGKB"]),
    "paris": CityConfig(
        slug="paris", display="Paris", unit="C", timezone="Europe/Paris",
        resolves_at_icao="LFPB", resolves_at_name="Paris-Le Bourget",
        resolves_at_lat=48.9694, resolves_at_lon=2.4414,
        center_lat=48.8566, center_lon=2.3522,
        explicit_airports=["LFPG", "LFPO", "LFPB", "LFPN", "LFPM", "LFOB",
                           "LFPV"]),
    "madrid": CityConfig(
        slug="madrid", display="Madrid", unit="C", timezone="Europe/Madrid",
        resolves_at_icao="LEMD", resolves_at_name="Adolfo Suárez Madrid-Barajas",
        resolves_at_lat=40.4719, resolves_at_lon=-3.5626,
        center_lat=40.4168, center_lon=-3.7038,
        explicit_airports=["LEMD", "LECU", "LETO"]),
    "warsaw": CityConfig(
        slug="warsaw", display="Warsaw", unit="C", timezone="Europe/Warsaw",
        resolves_at_icao="EPWA", resolves_at_name="Warsaw Chopin",
        resolves_at_lat=52.1657, resolves_at_lon=20.9671,
        center_lat=52.2297, center_lon=21.0122,
        explicit_airports=["EPWA", "EPMO", "EPBC"]),
    "moscow": CityConfig(
        slug="moscow", display="Moscow", unit="C", timezone="Europe/Moscow",
        resolves_at_icao="UUWW", resolves_at_name="Vnukovo",
        resolves_at_lat=55.5915, resolves_at_lon=37.2615,
        center_lat=55.7558, center_lon=37.6173,
        explicit_airports=["UUWW", "UUEE", "UUDD", "UUMU"]),
    "helsinki": CityConfig(
        slug="helsinki", display="Helsinki", unit="C",
        timezone="Europe/Helsinki",
        resolves_at_icao="EFHK", resolves_at_name="Helsinki-Vantaa",
        resolves_at_lat=60.3172, resolves_at_lon=24.9633,
        center_lat=60.1699, center_lon=24.9384,
        explicit_airports=["EFHK", "EFHF"]),
    "ankara": CityConfig(
        slug="ankara", display="Ankara", unit="C",
        timezone="Europe/Istanbul",
        resolves_at_icao="LTAC", resolves_at_name="Esenboğa",
        resolves_at_lat=40.1281, resolves_at_lon=32.9951,
        center_lat=39.9334, center_lon=32.8597,
        explicit_airports=["LTAC", "LTAE"]),
    # ── Middle East ──
    "tel-aviv": CityConfig(
        slug="tel-aviv", display="Tel Aviv", unit="C",
        timezone="Asia/Jerusalem",
        resolves_at_icao="LLBG", resolves_at_name="Ben Gurion",
        resolves_at_lat=32.0114, resolves_at_lon=34.8867,
        center_lat=32.0853, center_lon=34.7818,
        explicit_airports=["LLBG", "LLSD"]),
    # ── Asia ──
    "tokyo": CityConfig(
        slug="tokyo", display="Tokyo", unit="C", timezone="Asia/Tokyo",
        resolves_at_icao="RJTT", resolves_at_name="Tokyo Haneda",
        resolves_at_lat=35.5494, resolves_at_lon=139.7798,
        center_lat=35.6762, center_lon=139.6503,
        explicit_airports=["RJTT", "RJAA", "RJAH", "RJTL", "RJTC"]),
    "seoul": CityConfig(
        slug="seoul", display="Seoul", unit="C", timezone="Asia/Seoul",
        resolves_at_icao="RKSI", resolves_at_name="Incheon Intl",
        resolves_at_lat=37.4602, resolves_at_lon=126.4407,
        center_lat=37.5665, center_lon=126.9780,
        explicit_airports=["RKSI", "RKSS", "RKPC"]),
    "busan": CityConfig(
        slug="busan", display="Busan", unit="C", timezone="Asia/Seoul",
        resolves_at_icao="RKPK", resolves_at_name="Gimhae Intl",
        resolves_at_lat=35.1795, resolves_at_lon=128.9381,
        center_lat=35.1796, center_lon=129.0756,
        explicit_airports=["RKPK", "RKPU"]),
    "shanghai": CityConfig(
        slug="shanghai", display="Shanghai", unit="C", timezone="Asia/Shanghai",
        resolves_at_icao="ZSPD", resolves_at_name="Pudong Intl",
        resolves_at_lat=31.1443, resolves_at_lon=121.8083,
        center_lat=31.2304, center_lon=121.4737,
        explicit_airports=["ZSPD", "ZSSS"]),
    "beijing": CityConfig(
        slug="beijing", display="Beijing", unit="C", timezone="Asia/Shanghai",
        resolves_at_icao="ZBAA", resolves_at_name="Beijing Capital Intl",
        resolves_at_lat=40.0801, resolves_at_lon=116.5846,
        center_lat=39.9042, center_lon=116.4074,
        explicit_airports=["ZBAA", "ZBAD", "ZBNY"]),
    "shenzhen": CityConfig(
        slug="shenzhen", display="Shenzhen", unit="C",
        timezone="Asia/Shanghai",
        resolves_at_icao="ZGSZ", resolves_at_name="Shenzhen Bao'an Intl",
        resolves_at_lat=22.6393, resolves_at_lon=113.8108,
        center_lat=22.5431, center_lon=114.0579,
        explicit_airports=["ZGSZ"]),
    "guangzhou": CityConfig(
        slug="guangzhou", display="Guangzhou", unit="C",
        timezone="Asia/Shanghai",
        resolves_at_icao="ZGGG", resolves_at_name="Baiyun Intl",
        resolves_at_lat=23.3924, resolves_at_lon=113.2988,
        center_lat=23.1291, center_lon=113.2644,
        explicit_airports=["ZGGG"]),
    "wuhan": CityConfig(
        slug="wuhan", display="Wuhan", unit="C", timezone="Asia/Shanghai",
        resolves_at_icao="ZHHH", resolves_at_name="Tianhe Intl",
        resolves_at_lat=30.7838, resolves_at_lon=114.2081,
        center_lat=30.5928, center_lon=114.3055,
        explicit_airports=["ZHHH"]),
    "qingdao": CityConfig(
        slug="qingdao", display="Qingdao", unit="C",
        timezone="Asia/Shanghai",
        resolves_at_icao="ZSQD", resolves_at_name="Jiaodong Intl",
        resolves_at_lat=36.3614, resolves_at_lon=120.0942,
        center_lat=36.0671, center_lon=120.3826,
        explicit_airports=["ZSQD"]),
    "hong-kong": CityConfig(
        slug="hong-kong", display="Hong Kong", unit="C",
        timezone="Asia/Hong_Kong",
        # Hong Kong resolves at the HK Observatory, not the airport.
        # We use the Observatory's lat/lon as the model location.
        resolves_at_icao="VHHH",
        resolves_at_name="Hong Kong Observatory",
        resolves_at_lat=22.3023, resolves_at_lon=114.1742,
        center_lat=22.3193, center_lon=114.1694,
        explicit_airports=["VHHH", "VHHX"]),
    "taipei": CityConfig(
        slug="taipei", display="Taipei", unit="C", timezone="Asia/Taipei",
        resolves_at_icao="RCSS", resolves_at_name="Taipei Songshan",
        resolves_at_lat=25.0697, resolves_at_lon=121.5519,
        center_lat=25.0330, center_lon=121.5654,
        explicit_airports=["RCSS", "RCTP"]),
    "singapore": CityConfig(
        slug="singapore", display="Singapore", unit="C",
        timezone="Asia/Singapore",
        resolves_at_icao="WSSS", resolves_at_name="Changi Intl",
        resolves_at_lat=1.3644, resolves_at_lon=103.9915,
        center_lat=1.3521, center_lon=103.8198,
        explicit_airports=["WSSS", "WSAP", "WSSL"]),
    "manila": CityConfig(
        slug="manila", display="Manila", unit="C",
        timezone="Asia/Manila",
        resolves_at_icao="RPLL", resolves_at_name="Ninoy Aquino Intl",
        resolves_at_lat=14.5086, resolves_at_lon=121.0194,
        center_lat=14.5995, center_lon=120.9842,
        explicit_airports=["RPLL", "RPLB", "RPLC"]),
    "jakarta": CityConfig(
        slug="jakarta", display="Jakarta", unit="C",
        timezone="Asia/Jakarta",
        resolves_at_icao="WIHH", resolves_at_name="Halim Perdanakusuma Intl",
        resolves_at_lat=-6.2664, resolves_at_lon=106.8909,
        center_lat=-6.2088, center_lon=106.8456,
        explicit_airports=["WIHH", "WIII"]),
    # ── Africa ──
    "cape-town": CityConfig(
        slug="cape-town", display="Cape Town", unit="C",
        timezone="Africa/Johannesburg",
        resolves_at_icao="FACT", resolves_at_name="Cape Town Intl",
        resolves_at_lat=-33.9648, resolves_at_lon=18.6017,
        center_lat=-33.9249, center_lon=18.4241,
        explicit_airports=["FACT"]),
    # ── Oceania ──
    "wellington": CityConfig(
        slug="wellington", display="Wellington", unit="C",
        timezone="Pacific/Auckland",
        resolves_at_icao="NZWN", resolves_at_name="Wellington Intl",
        resolves_at_lat=-41.3272, resolves_at_lon=174.8053,
        center_lat=-41.2866, center_lon=174.7756,
        explicit_airports=["NZWN"]),
}


# Build reverse lookup: ICAO -> (city-key, unit). Direct/explicit only.
ICAO_TO_CITY: Dict[str, Tuple[str, str]] = {}
for ck, cfg in SUPPORTED_CITIES.items():
    for icao in cfg.explicit_airports:
        ICAO_TO_CITY[icao] = (ck, cfg.unit)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def city_for_airport(icao: str) -> Optional[Tuple[str, str]]:
    """Direct mapping only. Returns (city_key, unit) or None."""
    return ICAO_TO_CITY.get((icao or "").upper())


def nearest_city_to_point(
    lat: float, lon: float, max_km: float = GEO_FALLBACK_RADIUS_KM
) -> Optional[Tuple[str, str, float]]:
    """Geographic fallback. Returns (city_key, unit, distance_km) or None."""
    best = None
    best_d = max_km
    for ck, cfg in SUPPORTED_CITIES.items():
        d = _haversine_km(lat, lon, cfg.center_lat, cfg.center_lon)
        if d < best_d:
            best = (ck, cfg.unit, d)
            best_d = d
    return best


def resolve_city_for_airport(
    icao: str, lat: float, lon: float
) -> Optional[Tuple[str, str, str]]:
    """Two-tier lookup: explicit map then geographic.

    Returns (city_key, unit, source) where source is 'explicit' or 'geo',
    or None if nothing within range.
    """
    direct = city_for_airport(icao)
    if direct:
        return (direct[0], direct[1], "explicit")
    near = nearest_city_to_point(lat, lon)
    if near:
        return (near[0], near[1], "geo")
    return None


def city_local_today(city_key: str) -> Optional[date]:
    """The current calendar date in the city's local timezone."""
    cfg = SUPPORTED_CITIES.get(city_key)
    if not cfg:
        return None
    return datetime.now(ZoneInfo(cfg.timezone)).date()


# ─────────────────────────── bucket dataclasses ───────────────────────────
@dataclass
class TempBucket:
    label: str
    value: int                      # exact value, or LOW for ranges
    kind: str                       # 'exact' | 'range' | 'lte' | 'gte'
    yes_prob: float                 # 0..1
    market_slug: str
    high_value: Optional[int] = None

    @property
    def hi(self) -> int:
        return self.high_value if self.high_value is not None else self.value

    @property
    def midpoint(self) -> float:
        return (self.value + self.hi) / 2.0

    def matches(self, predicted: int) -> bool:
        if self.kind == "exact": return predicted == self.value
        if self.kind == "range": return self.value <= predicted <= self.hi
        if self.kind == "gte":   return predicted >= self.value
        if self.kind == "lte":   return predicted <= self.value
        return False

    @property
    def trade_url(self) -> str:
        return f"{SITE_BASE}/market/{self.market_slug}"


@dataclass
class CityMarket:
    city_key: str
    city_display: str
    unit: str
    event_slug: str
    event_title: str
    target_date: date
    buckets: List[TempBucket]
    resolves_at_icao: str
    resolves_at_name: str

    @property
    def event_url(self) -> str:
        return f"{SITE_BASE}/event/{self.event_slug}"


# ─────────────────────────── parsing ──────────────────────────────────────
_MONTH_NAMES = [m.lower() for m in calendar.month_name[1:]]


def _candidate_event_slugs(city_slug: str, d: date) -> List[str]:
    """Polymarket's 2026 worded format dominates; numeric is a fallback."""
    month_name = _MONTH_NAMES[d.month - 1]
    yyyy_mm_dd = d.strftime("%Y-%m-%d")
    yymmdd = d.strftime("%y%m%d")
    return [
        f"highest-temperature-in-{city_slug}-on-{month_name}-{d.day}-{d.year}",
        f"highest-temperature-in-{city_slug}-{month_name}-{d.day}-{d.year}",
        f"highest-temperature-in-{city_slug}-on-{month_name}-{d.day}",
        f"highest-temperature-{city_slug}-{month_name}-{d.day}-{d.year}",
        f"highest-temperature-in-{city_slug}-on-{yyyy_mm_dd}",
        f"highest-temperature-in-{city_slug}-{yyyy_mm_dd}",
        f"highest-temperature-{city_slug}-{yymmdd}",
    ]


_RANGE_RE = re.compile(
    r"(-?\d{1,3})\s*-\s*(-?\d{1,3})\s*[°º]?\s*([CF])\b", re.IGNORECASE)
_OPEN_RE = re.compile(
    r"(-?\d{1,3})\s*[°º]?\s*([CF])\b\s*"
    r"or\s+(?P<qual>above|higher|more|below|lower|less)", re.IGNORECASE)
_SINGLE_RE = re.compile(r"(-?\d{1,3})\s*[°º]?\s*([CF])\b", re.IGNORECASE)


def _parse_bucket(question: str) -> Optional[Tuple[int, int, str, str]]:
    """Returns (low_value, high_value, kind, unit) or None."""
    if not question:
        return None
    m = _OPEN_RE.search(question)
    if m:
        try:
            v = int(m.group(1))
        except ValueError:
            return None
        unit = m.group(2).upper()
        qual = (m.group("qual") or "").lower()
        kind = "gte" if qual in ("above", "higher", "more") else "lte"
        return v, v, kind, unit
    m = _RANGE_RE.search(question)
    if m:
        try:
            lo, hi = int(m.group(1)), int(m.group(2))
        except ValueError:
            return None
        if lo > hi: lo, hi = hi, lo
        return lo, hi, "range", m.group(3).upper()
    m = _SINGLE_RE.search(question)
    if m:
        try:
            v = int(m.group(1))
        except ValueError:
            return None
        return v, v, "exact", m.group(2).upper()
    return None


def _parse_outcome_prices(raw) -> List[float]:
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        try:
            import json
            items = json.loads(s)
        except Exception:
            return []
    else:
        return []
    out = []
    for it in items:
        try:
            out.append(float(it))
        except (TypeError, ValueError):
            pass
    return out


# ─────────────────────────── fetcher ──────────────────────────────────────
async def _fetch_event(client: httpx.AsyncClient, slug: str) -> Optional[dict]:
    try:
        r = await client.get(EVENT_BY_SLUG.format(slug=slug))
    except httpx.HTTPError as e:
        log.debug("polymarket: HTTP error for slug=%s: %s", slug, e)
        return None
    if r.status_code == 404:
        log.debug("polymarket: 404 for slug=%s", slug)
        return None
    if r.status_code != 200:
        log.warning("polymarket: HTTP %s for slug=%s", r.status_code, slug)
        return None
    try:
        data = r.json()
    except Exception:
        return None
    if not isinstance(data, dict) or not data.get("markets"):
        return None
    return data


async def get_market_for_city(
    city_key: str, target_date: date
) -> Optional[CityMarket]:
    cfg = SUPPORTED_CITIES.get(city_key)
    if not cfg:
        return None
    unit = cfg.unit
    slugs = _candidate_event_slugs(cfg.slug, target_date)
    event = None
    matched_slug = None
    async with httpx.AsyncClient(timeout=10) as client:
        for s in slugs:
            event = await _fetch_event(client, s)
            if event:
                matched_slug = s
                log.info("polymarket: matched %s for %s/%s", s, city_key,
                         target_date)
                break
    if not event:
        log.info("polymarket: no event for %s/%s (tried %d slugs)",
                 city_key, target_date, len(slugs))
        return None

    buckets: List[TempBucket] = []
    for m in event.get("markets") or []:
        if m.get("closed") is True or m.get("active") is False:
            continue
        parsed = _parse_bucket(m.get("question") or "")
        if not parsed:
            continue
        lo, hi, kind, m_unit = parsed
        if m_unit != unit:
            unit = m_unit
        prices = _parse_outcome_prices(m.get("outcomePrices"))
        if prices:
            yes_p = prices[0]
        else:
            try:
                yes_p = float(m.get("bestBid") or 0)
            except (TypeError, ValueError):
                continue
        if not (0 <= yes_p <= 1):
            continue
        if kind == "exact":   label = f"{lo}°{unit}"
        elif kind == "range": label = f"{lo}–{hi}°{unit}"
        elif kind == "gte":   label = f"≥{lo}°{unit}"
        else:                 label = f"≤{lo}°{unit}"
        buckets.append(TempBucket(
            label=label, value=lo,
            high_value=hi if kind == "range" else None,
            kind=kind, yes_prob=yes_p,
            market_slug=m.get("slug") or matched_slug,
        ))

    if not buckets:
        log.warning("polymarket: matched %s but parsed 0 buckets", matched_slug)
        return None
    log.info("polymarket: %s → %d buckets", matched_slug, len(buckets))

    return CityMarket(
        city_key=city_key, city_display=cfg.display, unit=unit,
        event_slug=matched_slug, event_title=event.get("title") or "",
        target_date=target_date, buckets=buckets,
        resolves_at_icao=cfg.resolves_at_icao,
        resolves_at_name=cfg.resolves_at_name,
    )


# ─────────────────────────── selectors ────────────────────────────────────
def top_n_by_yes(market: CityMarket, n: int = 3) -> List[TempBucket]:
    return sorted(market.buckets, key=lambda b: -b.yes_prob)[:n]


def match_for_prediction(market: CityMarket, predicted: int) -> Optional[TempBucket]:
    for b in market.buckets:
        if b.kind == "exact" and b.value == predicted:
            return b
    for b in market.buckets:
        if b.kind == "range" and b.value <= predicted <= b.hi:
            return b
    for b in market.buckets:
        if b.kind in ("gte", "lte") and b.matches(predicted):
            return b
    return None


def hedges_around(market: CityMarket, predicted: int,
                  target_count: int = 3) -> List[TempBucket]:
    closed = sorted([b for b in market.buckets if b.kind in ("exact", "range")],
                    key=lambda b: b.midpoint)
    if not closed:
        return []
    center = min(range(len(closed)),
                 key=lambda i: abs(closed[i].midpoint - predicted))
    picked = {center}
    lo_idx, hi_idx = center - 1, center + 1
    while len(picked) < target_count and (lo_idx >= 0 or hi_idx < len(closed)):
        lo_d = abs(closed[lo_idx].midpoint - predicted) if lo_idx >= 0 else float("inf")
        hi_d = abs(closed[hi_idx].midpoint - predicted) if hi_idx < len(closed) else float("inf")
        if lo_d <= hi_d:
            picked.add(lo_idx); lo_idx -= 1
        else:
            picked.add(hi_idx); hi_idx += 1
    band = [closed[i] for i in sorted(picked)]
    for b in market.buckets:
        if b.kind in ("gte", "lte") and b.matches(predicted):
            if not any(x.market_slug == b.market_slug for x in band):
                band.append(b)
    return band


def supported_cities_alphabetical() -> List[Tuple[str, CityConfig]]:
    """Return [(city_key, config)] sorted by display name."""
    return sorted(SUPPORTED_CITIES.items(), key=lambda kv: kv[1].display)


# ─────────────────────────── EV & opportunity scoring ─────────────────────
import math as _math


def _gauss_cdf(z: float) -> float:
    return 0.5 * (1.0 + _math.erf(z / _math.sqrt(2)))


def model_yes_prob_for_bucket(
    bucket: TempBucket, predicted: float, sigma_unit: float
) -> float:
    """Our model's probability that the actual high will land inside this
    bucket — i.e. our YES probability. Computed as the integral of a Gaussian
    centered on the model's predicted temperature with std `sigma_unit` (in
    the same unit as the bucket) over the bucket's interval.
    """
    sigma = max(0.1, sigma_unit)
    if bucket.kind == "exact":
        lo, hi = bucket.value - 0.5, bucket.value + 0.5
    elif bucket.kind == "range":
        lo, hi = bucket.value - 0.5, bucket.hi + 0.5
    elif bucket.kind == "gte":
        lo, hi = bucket.value - 0.5, predicted + 50  # tail
    elif bucket.kind == "lte":
        lo, hi = predicted - 50, bucket.value + 0.5  # tail
    else:
        return 0.0
    z_lo = (lo - predicted) / sigma
    z_hi = (hi - predicted) / sigma
    p = _gauss_cdf(z_hi) - _gauss_cdf(z_lo)
    return max(0.0, min(1.0, p))


@dataclass
class EVPick:
    """Expected-value-evaluated bucket. EV is per $1 staked on YES at the
    market's current YES price: EV = model_p / yes_price - 1. Positive EV
    means the model thinks the bucket is mispriced cheap.
    """
    bucket: TempBucket
    model_p: float        # our model's YES probability for this bucket
    market_p: float       # market's YES probability (last trade)
    edge_pp: float        # (model_p - market_p) * 100, in percentage points
    ev_per_dollar: float  # model_p / market_p - 1   (signed, can be negative)
    score: float          # combined score for ranking


def rank_buckets_by_ev(
    market: CityMarket, predicted: float, sigma_unit: float
) -> List[EVPick]:
    """Return all buckets ranked by EV. Best (highest EV) first. We skip
    buckets priced under 2¢ (illiquid noise) and over 98¢ (no upside).
    """
    out: List[EVPick] = []
    for b in market.buckets:
        if not (0.02 <= b.yes_prob <= 0.98):
            continue
        mp = model_yes_prob_for_bucket(b, predicted, sigma_unit)
        if mp < 0.01:
            continue
        ev = (mp / b.yes_prob) - 1.0
        edge_pp = (mp - b.yes_prob) * 100.0
        # Combined score: edge in pp scaled by our model_p (we want both a
        # mispricing and decent absolute confidence in the bucket).
        score = edge_pp * mp
        out.append(EVPick(
            bucket=b, model_p=mp, market_p=b.yes_prob,
            edge_pp=edge_pp, ev_per_dollar=ev, score=score,
        ))
    out.sort(key=lambda p: -p.score)
    return out


@dataclass
class Opportunity:
    """A high-confidence trading opportunity for a (city, day) combo."""
    city_key: str
    city_display: str
    target_date: date
    is_today: bool
    confidence: float          # model's confidence for the day (0..1)
    predicted_unit: int        # rounded prediction in market's unit (°F or °C)
    unit: str                  # 'C' or 'F'
    matched_bucket: TempBucket
    matched_yes: float         # market's YES on the matched bucket
    best_pick: EVPick          # top EV pick (could be matched bucket or another)
    hedge_pick: Optional[EVPick]
    market: CityMarket
    score: float               # combined ranking score across all opportunities


def score_opportunity(opp_confidence: float, best_ev_score: float) -> float:
    """Combined score for ranking opportunities. We multiply confidence by
    the best EV's score (which is already edge*model_p) so we surface
    high-confidence days where the market also has decent mispricing.
    """
    return opp_confidence * max(0.0, best_ev_score)


