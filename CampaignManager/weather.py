"""The map's weather layer: live weather of real places, from Open-Meteo (free, no API key).

Fetched on the server, for all the stale regions of a map in one request, and kept in
each WeatherRegion (`weather`, `weather_at`) for WEATHER_TTL: pages within that time do not
call the API. If the API is down, the regions keep their last values (shown as old) and
the API is not called again for RETRY_AFTER. Day and night come from the API's `is_day`
at each real place; the time shown is the campaign's one clock (Campaign.time_zone).
"""
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone
from django.utils.translation import gettext_noop

log = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
CURRENT = "temperature_2m,is_day,weather_code,wind_speed_10m,precipitation"
TIMEOUT = 6  # seconds
WEATHER_TTL = timedelta(minutes=20)
RETRY_AFTER = 300  # seconds without calling the API after it failed
FAILED_KEY = "weather:failed"
WINDY = 20  # km/h: from here the wind is shown
USER_AGENT = "KirokuStory/1.0 (Django)"

# WMO weather codes (Open-Meteo's `weather_code`) -> (icon, label).
CONDITIONS = [
    ((0,), "☀️", gettext_noop("Despejado")),
    ((1, 2), "⛅", gettext_noop("Algunas nubes")),
    ((3,), "☁️", gettext_noop("Nublado")),
    ((45, 48), "🌫️", gettext_noop("Niebla")),
    ((51, 53, 55, 56, 57), "🌦", gettext_noop("Llovizna")),
    ((61, 63, 65, 66, 67), "🌧", gettext_noop("Lluvia")),
    ((71, 73, 75, 77), "❄️", gettext_noop("Nieve")),
    ((80, 81, 82), "🌦", gettext_noop("Chubascos")),
    ((85, 86), "🌨", gettext_noop("Chubascos de nieve")),
    ((95, 96, 99), "⛈️", gettext_noop("Tormenta")),
]


class WeatherUnavailable(Exception):
    """The weather or geocoding API did not answer, or answered something unexpected."""


def _get_json(url, params):
    """GET a JSON API. Raises WeatherUnavailable on any network or format problem."""
    full = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(full, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # nosec B310: fixed https URLs
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        raise WeatherUnavailable(str(error)) from error


# At night a clear or partly cloudy sky is not drawn with a sun.
NIGHT_ICONS = {0: "✨", 1: "☁️", 2: "☁️"}


def condition(code, is_day=True):
    """(icon, label) of a WMO weather code."""
    for codes, icon, label in CONDITIONS:
        if code in codes:
            return (icon if is_day else NIGHT_ICONS.get(code, icon)), label
    return "·", gettext_noop("Sin datos")


def fetch_current(coordinates):
    """Current weather at each (latitude, longitude), in one request, as a list of dicts:
    temperature (°C), is_day (bool), code (WMO), wind (km/h), precipitation (mm), time."""
    if not coordinates:
        return []
    data = _get_json(FORECAST_URL, {
        "latitude": ",".join(f"{lat:.4f}" for lat, _ in coordinates),
        "longitude": ",".join(f"{lon:.4f}" for _, lon in coordinates),
        "current": CURRENT, "timezone": "UTC",
    })
    answers = data if isinstance(data, list) else [data]
    if len(answers) != len(coordinates):
        raise WeatherUnavailable("Open-Meteo answered for another number of places.")
    result = []
    for answer in answers:
        try:
            current = answer["current"]
            result.append({
                "temperature": round(float(current["temperature_2m"])),
                "is_day": bool(current["is_day"]),
                "code": int(current["weather_code"]),
                "wind": round(float(current.get("wind_speed_10m") or 0)),
                "precipitation": round(float(current.get("precipitation") or 0), 1),
                "time": current.get("time", ""),
            })
        except (KeyError, TypeError, ValueError) as error:
            raise WeatherUnavailable(f"Unexpected answer: {error}") from error
    return result


def refresh(regions, now=None):
    """Fetch the weather of the regions whose last values are older than WEATHER_TTL, in one
    request, and save it. If the API fails, nothing changes (the old values stay) and it is
    not called again for RETRY_AFTER. Returns True if the API answered (or was not needed)."""
    now = now or timezone.now()
    stale = [r for r in regions if r.weather_at is None or now - r.weather_at >= WEATHER_TTL]
    if not stale:
        return True
    if cache.get(FAILED_KEY):
        return False
    places = sorted({(r.latitude, r.longitude) for r in stale})
    try:
        answers = dict(zip(places, fetch_current(places)))
    except WeatherUnavailable as error:
        log.warning("Open-Meteo is not answering: %s", error)
        cache.set(FAILED_KEY, True, RETRY_AFTER)
        return False
    for region in stale:
        region.weather, region.weather_at = answers[(region.latitude, region.longitude)], now
        region.save(update_fields=["weather", "weather_at"])
    return True


def describe(region, now=None):
    """What the layer shows for a region: icon, label, temperature, wind and rain when they
    matter, day or night, and how old the data is. None if it was never fetched."""
    weather = region.weather
    if not weather:
        return None
    now = now or timezone.now()
    icon, label = condition(weather.get("code"), weather.get("is_day", True))
    age = now - region.weather_at if region.weather_at else None
    return {
        "icon": icon, "label": label, "temperature": weather.get("temperature"),
        "is_day": weather.get("is_day", True),
        "wind": weather.get("wind") if (weather.get("wind") or 0) >= WINDY else None,
        "precipitation": weather.get("precipitation") if (weather.get("precipitation") or 0) > 0 else None,
        "old_minutes": int(age.total_seconds() // 60) if age is not None and age >= WEATHER_TTL else None,
    }


# ---------------------------------------------------------------------------
# Finding the real place
# ---------------------------------------------------------------------------

COORDINATES = re.compile(r"^\s*(-?\d+(?:[.,]\d+)?)\s*[,; ]\s*(-?\d+(?:[.,]\d+)?)\s*$")


def parse_coordinates(text):
    """(latitude, longitude) from «43.26, -2.93» (or with ;), or None."""
    match = COORDINATES.match(text or "")
    if not match:
        return None
    lat, lon = (float(v.replace(",", ".")) for v in match.groups())
    return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None


def search_places(text, limit=6):
    """Real places matching a text: «Bilbao», «Bilbao, España», «Francia». The part after the
    first comma narrows the results (country or region). Coordinates are returned as they
    are. Each result: name (to show), latitude, longitude. Raises WeatherUnavailable."""
    coordinates = parse_coordinates(text)
    if coordinates:
        lat, lon = coordinates
        return [{"name": f"{lat:.4f}, {lon:.4f}", "latitude": lat, "longitude": lon}]
    name, _, rest = (text or "").partition(",")
    name, rest = name.strip(), rest.strip().lower()
    if len(name) < 2:
        return []
    data = _get_json(GEOCODING_URL, {"name": name, "count": 20, "language": "es", "format": "json"})
    results = []
    for place in data.get("results") or []:
        parts = [place.get("name"), place.get("admin1"), place.get("country")]
        label = ", ".join(dict.fromkeys(p for p in parts if p))
        if rest and rest not in label.lower():
            continue
        results.append({"name": label, "latitude": place["latitude"], "longitude": place["longitude"]})
    return results[:limit]
