# weather_client.py
import requests
from datetime import datetime, date
from zoneinfo import ZoneInfo


def fetch_today_weather_metrics(lat: float, lon: float, timezone: str) -> dict:
    """
    使用 Open-Meteo（免 key）取得今日每小時 UV / 相對濕度，計算：
      - max_uv
      - min_humidity / max_humidity / humidity_range

    回傳：
    {
      "max_uv": float|None,
      "min_humidity": float|None,
      "max_humidity": float|None,
      "humidity_range": float|None
    }
    """
    tz = ZoneInfo(timezone)
    today = datetime.now(tz).date()

    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "uv_index,relative_humidity_2m",
        "timezone": timezone,
        "forecast_days": 1,
    }

    r = requests.get(url, params=params, timeout=12)
    r.raise_for_status()
    data = r.json()

    hourly = data.get("hourly", {}) or {}
    times = hourly.get("time", []) or []
    uv = hourly.get("uv_index", []) or []
    rh = hourly.get("relative_humidity_2m", []) or []

    # 過濾出「今天」的 hour
    uv_today = []
    rh_today = []

    for i, ts in enumerate(times):
        # ts e.g. "2026-01-22T10:00"
        try:
            dt = datetime.fromisoformat(ts).replace(tzinfo=tz)
        except Exception:
            continue
        if dt.date() != today:
            continue

        if i < len(uv) and uv[i] is not None:
            try:
                uv_today.append(float(uv[i]))
            except Exception:
                pass

        if i < len(rh) and rh[i] is not None:
            try:
                rh_today.append(float(rh[i]))
            except Exception:
                pass

    max_uv = max(uv_today) if uv_today else None
    min_h = min(rh_today) if rh_today else None
    max_h = max(rh_today) if rh_today else None
    h_range = (max_h - min_h) if (min_h is not None and max_h is not None) else None

    return {
        "max_uv": max_uv,
        "min_humidity": min_h,
        "max_humidity": max_h,
        "humidity_range": h_range,
    }
