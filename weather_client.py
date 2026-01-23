# weather_client.py
import datetime
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_SESSION = None

def _get_session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        s = requests.Session()
        retry = Retry(
            total=4,
            connect=4,
            read=4,
            backoff_factor=0.6,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        _SESSION = s
    return _SESSION

def fetch_today_weather_metrics(lat: float, lon: float, timezone: str = "Asia/Taipei") -> dict:
    tz = ZoneInfo(timezone)
    now = datetime.datetime.now(tz)
    today = now.date()

    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "timezone": timezone,
        "forecast_days": 1,
        # 溫度/體感 + UV + 濕度
        "hourly": "temperature_2m,apparent_temperature,uv_index,relative_humidity_2m",
        # 直接拿 current，避免你自己找最近一小時
        "current": "temperature_2m,apparent_temperature",
    }

    s = _get_session()
    try:
        r = s.get(url, params=params, timeout=20)
        r.raise_for_status()
    except requests.exceptions.SSLError:
        # SSLEOF 通常是短暫握手問題：重建 session 再試一次
        global _SESSION
        _SESSION = None
        s = _get_session()
        r = s.get(url, params=params, timeout=20)
        r.raise_for_status()

    data = r.json()
    hourly = data.get("hourly") or {}

    times = hourly.get("time") or []
    t2m = hourly.get("temperature_2m") or []
    app = hourly.get("apparent_temperature") or []
    uv = hourly.get("uv_index") or []
    rh = hourly.get("relative_humidity_2m") or []

    idxs = []
    for i, ts in enumerate(times):
        try:
            dt = datetime.datetime.fromisoformat(ts).replace(tzinfo=tz)
            if dt.date() == today:
                idxs.append(i)
        except Exception:
            continue

    def _today_vals(arr):
        out = []
        for i in idxs:
            if i < len(arr) and arr[i] is not None:
                try:
                    out.append(float(arr[i]))
                except Exception:
                    pass
        return out

    tvals = _today_vals(t2m)
    avals = _today_vals(app)
    uvvals = _today_vals(uv)
    rhvals = _today_vals(rh)

    cur = data.get("current") or {}
    temp_now = cur.get("temperature_2m", None)
    app_now = cur.get("apparent_temperature", None)

    try:
        temp_now = float(temp_now) if temp_now is not None else (tvals[-1] if tvals else None)
    except Exception:
        temp_now = (tvals[-1] if tvals else None)

    try:
        app_now = float(app_now) if app_now is not None else (avals[-1] if avals else None)
    except Exception:
        app_now = (avals[-1] if avals else None)

    min_temp = min(tvals) if tvals else None
    max_temp = max(tvals) if tvals else None
    min_app = min(avals) if avals else None
    max_app = max(avals) if avals else None

    max_uv = max(uvvals) if uvvals else None
    min_h = min(rhvals) if rhvals else None
    max_h = max(rhvals) if rhvals else None
    h_range = (max_h - min_h) if (min_h is not None and max_h is not None) else None

    return {
        "temp_now": temp_now,
        "min_temp": min_temp,
        "max_temp": max_temp,
        "app_temp_now": app_now,
        "min_app_temp": min_app,
        "max_app_temp": max_app,
        "max_uv": max_uv,
        "min_humidity": min_h,
        "max_humidity": max_h,
        "humidity_range": h_range,
    }
