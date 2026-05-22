import uuid
from datetime import datetime


def _parse_start(raw_start: str) -> datetime:
    # Strava returns ISO 8601, e.g. "2026-05-22T10:30:00Z"
    if raw_start.endswith("Z"):
        raw_start = raw_start[:-1] + "+00:00"
    return datetime.fromisoformat(raw_start)


def normalize_strava_activity(raw: dict, user_id: uuid.UUID) -> dict:
    """Map a raw Strava activity dict to Activity model kwargs."""
    distance = float(raw.get("distance") or 0.0)
    duration = int(raw.get("moving_time") or 0)
    avg_speed = raw.get("average_speed")
    avg_pace = None
    if distance > 0 and duration > 0:
        avg_pace = duration / (distance / 1000.0)
    return {
        "user_id": user_id,
        "strava_id": int(raw["id"]),
        "type": raw.get("type", "Unknown"),
        "name": raw.get("name", "Untitled"),
        "start_date": _parse_start(raw["start_date"]),
        "distance_m": distance,
        "duration_s": duration,
        "elevation_m": raw.get("total_elevation_gain"),
        "avg_heart_rate": raw.get("average_heartrate"),
        "max_heart_rate": raw.get("max_heartrate"),
        "avg_speed_ms": float(avg_speed) if avg_speed is not None else None,
        "avg_pace_s_km": avg_pace,
        "calories": raw.get("calories"),
        "kudos_count": raw.get("kudos_count") or 0,
        "suffer_score": raw.get("suffer_score"),
        "trainer": bool(raw.get("trainer", False)),
        "commute": bool(raw.get("commute", False)),
        "raw_payload": raw,
    }


def _body_battery_at(garmin_day: dict, target_dt: datetime) -> int | None:
    bb_list = garmin_day.get("body_battery") or []
    if not bb_list:
        return None
    arr = bb_list[0].get("bodyBatteryValuesArray") or []
    if not arr:
        return None
    target_ms = int(target_dt.timestamp() * 1000)
    last_before = None
    for entry in arr:
        if len(entry) < 2:
            continue
        ts, val = entry[0], entry[1]
        if ts <= target_ms:
            last_before = val
        else:
            break
    return last_before if last_before is not None else arr[0][1]


def _hrv_status(garmin_day: dict) -> str | None:
    summary = (garmin_day.get("hrv") or {}).get("hrvSummary") or {}
    status = summary.get("status")
    if status is None:
        return None
    s = str(status)
    return s[:20] if len(s) > 20 else s


def _sleep_score(garmin_day: dict) -> int | None:
    scores = (
        (garmin_day.get("sleep") or {}).get("dailySleepDTO") or {}
    ).get("sleepScores") or {}
    overall = scores.get("overall")
    if isinstance(overall, dict):
        overall = overall.get("value")
    if isinstance(overall, (int, float)):
        return int(overall)
    return None


def enrich_with_garmin(activity_dict: dict, garmin_day: dict | None) -> dict:
    if not garmin_day:
        return activity_dict
    activity_dict["body_battery_at_start"] = _body_battery_at(
        garmin_day, activity_dict["start_date"]
    )
    activity_dict["hrv_status_on_day"] = _hrv_status(garmin_day)
    activity_dict["sleep_score_prev_night"] = _sleep_score(garmin_day)
    return activity_dict
