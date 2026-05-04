"""
Ensemble temperature forecasting from 9 of the world's best NWP models.

Includes 8 models from Open-Meteo plus the OpenWeatherMap One Call 3.0 API.
Confidence is derived from inter-model spread (a calibrated proxy for 
predictive uncertainty). Per-degree probabilities are computed from a 
Gaussian fit over the ensemble.
"""
import math
import statistics
import aiohttp
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional

import httpx

# Import API key from your config file
try:
    from config import OPENWEATHER_API_KEY
except ImportError:
    OPENWEATHER_API_KEY = None

OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
AVIATION_WX_METAR = "https://aviationweather.gov/api/data/metar"

# Model -> weight (sums to ~1.0). 
# Weights adjusted to include OpenWeatherMap (owm) at 0.08.
MODEL_WEIGHTS: Dict[str, float] = {
    "ecmwf_ifs025": 0.20,         # ECMWF IFS (Physics-based gold standard)
    "ecmwf_aifs025": 0.16,        # ECMWF AIFS (AI model)
    "icon_seamless": 0.14,        # DWD ICON (Germany)
    "ukmo_seamless": 0.12,        # UK Met Office
    "gfs_seamless": 0.09,         # NOAA GFS
    "owm": 0.08,                  # OpenWeatherMap (Added)
    "meteofrance_seamless": 0.08,  # Météo-France
    "jma_seamless": 0.08,         # Japan Meteorological Agency
    "gem_seamless": 0.05,         # Environment Canada GEM
}

MODELS: List[str] = [m for m in MODEL_WEIGHTS.keys() if m != "owm"]

@dataclass
class DayForecast:
    date: date
    predicted_max_c: int
    predicted_max_f: int
    confidence: float
    confidence_level: str
    high_confidence: bool
    std_c: float
    ensemble_mean_c: float
    model_values_c: Dict[str, float]
    probability_c: Dict[int, float]
    probability_f: Dict[int, float]

@dataclass
class CurrentObs:
    source: str
    temp_c: Optional[float]
    temp_f: Optional[float]
    wind_kt: Optional[float]
    wind_dir: Optional[int]
    wx: Optional[str]
    raw: Optional[str]
    observed_at: Optional[str]

# ─────────────────────────── api fetchers ──────────────────────────────

async def fetch_owm_data(lat: float, lon: float, api_key: Optional[str]) -> Optional[float]:
    """Fetches daily max temp from OWM One Call 3.0."""
    if not api_key:
        return None
    url = f"https://api.openweathermap.org/data/3.0/onecall?lat={lat}&lon={lon}&appid={api_key}&units=metric&exclude=minutely,hourly,alerts"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url)
            if r.status_code == 200:
                data = r.json()
                return data['daily'][0]['temp']['max']
    except Exception:
        pass
    return None

# ─────────────────────────── ensemble forecast ──────────────────────────────

async def fetch_ensemble_forecast(
    lat: float,
    lon: float,
    days: int = 7,
) -> Optional[List[DayForecast]]:
    # 1. Fetch Open-Meteo Models
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_max",
        "temperature_unit": "celsius",
        "timezone": "auto",
        "forecast_days": max(1, min(days, 16)),
        "models": ",".join(MODELS),
    }
    
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(OPEN_METEO_FORECAST, params=params)
        r.raise_for_status()
        om_data = r.json()

    # 2. Fetch OpenWeatherMap (New Model)
    owm_temp = await fetch_owm_data(lat, lon, OPENWEATHER_API_KEY)

    daily = om_data.get("daily") or {}
    times = daily.get("time") or []
    if not times:
        return None

    forecasts: List[DayForecast] = []
    for i, t_str in enumerate(times):
        try:
            d = date.fromisoformat(t_str)
        except ValueError:
            continue
            
        model_values: Dict[str, float] = {}
        
        # Collect Open-Meteo results
        for m in MODELS:
            arr = daily.get(f"temperature_2m_max_{m}")
            if arr and i < len(arr) and arr[i] is not None:
                model_values[m] = float(arr[i])
        
        # Add OWM to today's forecast if available
        if i == 0 and owm_temp is not None:
            model_values["owm"] = owm_temp

        if not model_values:
            continue

        # 3. Weighted ensemble mean (Normalized for missing models)
        weight_sum = sum(MODEL_WEIGHTS[m] for m in model_values)
        weighted_mean = (
            sum(MODEL_WEIGHTS[m] * v for m, v in model_values.items()) / weight_sum
        )

        # 4. Spread & Confidence
        if len(model_values) >= 2:
            std_c = statistics.stdev(model_values.values())
        else:
            std_c = 1.5

        confidence = max(0.0, min(1.0, 1.0 - std_c / 3.0))
        level = "HIGH" if confidence >= 0.75 else "MEDIUM" if confidence >= 0.50 else "LOW"
        high_conf = std_c <= 1.0

        # 5. Rounding & Probability
        pred_c = int(round(weighted_mean))
        pred_f = int(round(weighted_mean * 9 / 5 + 32))
        prob_c = _integer_probs(weighted_mean, std_c)
        prob_f = _integer_probs(weighted_mean * 9 / 5 + 32, max(std_c * 1.8, 0.1))

        forecasts.append(
            DayForecast(
                date=d, predicted_max_c=pred_c, predicted_max_f=pred_f,
                confidence=confidence, confidence_level=level, high_confidence=high_conf,
                std_c=std_c, ensemble_mean_c=weighted_mean, model_values_c=model_values,
                probability_c=prob_c, probability_f=prob_f
            )
        )
    return forecasts

def _integer_probs(mean: float, std: float, n_each_side: int = 3) -> Dict[int, float]:
    if std <= 0.05:
        return {int(round(mean)): 1.0}
    probs = {}
    center = int(round(mean))
    for offset in range(-n_each_side, n_each_side + 1):
        T = center + offset
        z_lo = (T - 0.5 - mean) / std
        z_hi = (T + 0.5 - mean) / std
        p = _norm_cdf(z_hi) - _norm_cdf(z_lo)
        if p > 0.005:
            probs[T] = p
    return probs

def _norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2)))

# ─────────────────────────── current observations ──────────────────────────

async def fetch_current_observation(icao: str, lat: float, lon: float) -> Optional[CurrentObs]:
    metar = await _fetch_metar(icao)
    return metar if metar else await _fetch_open_meteo_current(lat, lon)

async def _fetch_metar(icao: str) -> Optional[CurrentObs]:
    params = {"ids": icao, "format": "json", "taf": "false", "hours": 2}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(AVIATION_WX_METAR, params=params)
            data = r.json()
            if data:
                m = data[0]
                temp_c = m.get("temp")
                return CurrentObs(
                    source="METAR", temp_c=temp_c,
                    temp_f=(temp_c * 1.8 + 32) if temp_c is not None else None,
                    wind_kt=m.get("wspd"), wind_dir=m.get("wdir"),
                    wx=m.get("wxString"), raw=m.get("rawOb"),
                    observed_at=m.get("reportTime")
                )
    except: pass
    return None

async def _fetch_open_meteo_current(lat: float, lon: float) -> Optional[CurrentObs]:
    params = {
        "latitude": lat, "longitude": lon,
        "current": "temperature_2m,wind_speed_10m,wind_direction_10m",
        "wind_speed_unit": "kn", "temperature_unit": "celsius", "timezone": "auto"
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(OPEN_METEO_FORECAST, params=params)
            c = r.json().get("current") or {}
            temp_c = c.get("temperature_2m")
            return CurrentObs(
                source="Open-Meteo", temp_c=temp_c,
                temp_f=(temp_c * 1.8 + 32) if temp_c is not None else None,
                wind_kt=c.get("wind_speed_10m"), wind_dir=c.get("wind_direction_10m"),
                wx=None, raw=None, observed_at=c.get("time")
            )
    except: pass
    return None
