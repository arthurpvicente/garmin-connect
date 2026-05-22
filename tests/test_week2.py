import uuid
from app.services.normalizer import normalize_strava_activity, enrich_with_garmin

USER_ID = uuid.uuid4()

SAMPLE_STRAVA = {
    'id': 123456,
    'name': 'Morning Run',
    'type': 'Run',
    'distance': 5000.0,
    'moving_time': 1800,
    'total_elevation_gain': 50.0,
    'average_heartrate': 145.0,
    'max_heartrate': 172.0,
    'start_date': '2026-05-20T07:00:00Z',
}


def test_normalize_strava_maps_fields():
    result = normalize_strava_activity(SAMPLE_STRAVA, user_id=USER_ID)
    assert result['user_id'] == USER_ID
    assert result['strava_id'] == 123456
    assert result['distance_m'] == 5000.0
    assert result['duration_s'] == 1800
    assert result['type'] == 'Run'
    assert result['elevation_m'] == 50.0
    assert result['avg_heart_rate'] == 145.0
    assert result['max_heart_rate'] == 172.0
    assert result['avg_pace_s_km'] == 360.0
    assert result['raw_payload'] == SAMPLE_STRAVA


def test_normalize_strava_missing_heartrate():
    raw = {**SAMPLE_STRAVA}
    raw.pop('average_heartrate')
    raw.pop('max_heartrate')
    result = normalize_strava_activity(raw, user_id=USER_ID)
    assert result['avg_heart_rate'] is None
    assert result['max_heart_rate'] is None


def test_enrich_with_garmin_body_battery():
    activity = normalize_strava_activity(SAMPLE_STRAVA, user_id=USER_ID)
    start_ms = int(activity['start_date'].timestamp() * 1000)
    garmin = {
        'body_battery': [{
            'bodyBatteryValuesArray': [
                [start_ms - 60_000, 60],
                [start_ms + 60_000, 80],
            ],
        }],
        'hrv': {'hrvSummary': {'status': 'BALANCED'}},
        'sleep': {'dailySleepDTO': {'sleepScores': {'overall': {'value': 84}}}},
    }
    enriched = enrich_with_garmin(activity, garmin)
    assert enriched['body_battery_at_start'] == 60
    assert enriched['hrv_status_on_day'] == 'BALANCED'
    assert enriched['sleep_score_prev_night'] == 84


def test_enrich_with_empty_garmin():
    activity = normalize_strava_activity(SAMPLE_STRAVA, user_id=USER_ID)
    enriched = enrich_with_garmin(activity, {})
    assert 'body_battery_at_start' not in enriched
    assert 'hrv_status_on_day' not in enriched
