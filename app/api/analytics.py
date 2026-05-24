import uuid
from datetime import datetime, timedelta, timezone
from collections import Counter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends
from app.core.database import get_db
from app.models.activity import Activity
from app.models.personal_record import PersonalRecord

RUN_TYPES      = {'Run', 'TrailRun', 'VirtualRun'}
WALK_TYPES     = {'Walk', 'Hike'}
RIDE_TYPES     = {'Ride', 'VirtualRide', 'EBikeRide', 'MountainBikeRide'}
STRENGTH_TYPES = {'WeightTraining', 'Workout', 'Crossfit'}
SWIM_TYPES     = {'Swim'}

DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']


def _categorize(activity_type: str) -> str:
    if activity_type in RUN_TYPES: return 'running'
    if activity_type in WALK_TYPES: return 'walking'
    if activity_type in RIDE_TYPES: return 'cycling'
    if activity_type in STRENGTH_TYPES: return 'strength'
    if activity_type in SWIM_TYPES: return 'swimming'
    return 'other'


def _week_bounds(week_offset: int = 0):
    """Return (monday, sunday_end) for the given week offset (0=current)."""
    today = datetime.now(timezone.utc)
    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
    monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    sunday_end = monday + timedelta(days=7)
    return monday, sunday_end


def _fmt_pace(s_per_km):
    if not s_per_km or s_per_km <= 0:
        return None
    m = int(s_per_km // 60)
    s = int(s_per_km % 60)
    return f"{m}'{s:02d}\""

router = APIRouter(prefix="/analytics", tags=["analytics"])


async def _compute_week_stats(user_id: uuid.UUID, week_offset: int, db: AsyncSession) -> dict:
    """Build the rich stats blob for a single week."""
    week_start, week_end = _week_bounds(week_offset)

    result = await db.execute(
        select(Activity)
        .where(Activity.user_id == user_id)
        .where(Activity.start_date >= week_start)
        .where(Activity.start_date < week_end)
        .order_by(Activity.start_date)
    )
    activities = result.scalars().all()

    # Bucket by category
    buckets: dict[str, list[Activity]] = {
        'running': [], 'walking': [], 'cycling': [], 'strength': [], 'swimming': [], 'other': []
    }
    for a in activities:
        buckets[_categorize(a.type)].append(a)

    def _distance_stats(items):
        total_km = sum(a.distance_m or 0 for a in items) / 1000
        elev = sum(a.elevation_m or 0 for a in items)
        longest = max((a.distance_m or 0 for a in items), default=0) / 1000
        paces = [a.avg_pace_s_km for a in items if a.avg_pace_s_km]
        avg_pace = sum(paces) / len(paces) if paces else None
        duration = sum(a.duration_s or 0 for a in items)
        return {
            'sessions': len(items),
            'total_km': round(total_km, 1),
            'total_elevation_m': round(elev),
            'longest_km': round(longest, 1),
            'avg_pace_s_km': round(avg_pace) if avg_pace else None,
            'avg_pace_fmt': _fmt_pace(avg_pace),
            'total_duration_min': round(duration / 60),
        }

    def _duration_stats(items):
        duration = sum(a.duration_s or 0 for a in items)
        return {
            'sessions': len(items),
            'total_duration_min': round(duration / 60),
        }

    # Sleep + recovery (from Garmin enrichment per activity)
    sleep_scores = [a.sleep_score_prev_night for a in activities if a.sleep_score_prev_night]
    avg_sleep = round(sum(sleep_scores) / len(sleep_scores)) if sleep_scores else None
    batteries = [a.body_battery_at_start for a in activities if a.body_battery_at_start]
    avg_battery = round(sum(batteries) / len(batteries)) if batteries else None
    hrv_statuses = [a.hrv_status_on_day for a in activities if a.hrv_status_on_day]
    hrv_counts = dict(Counter(hrv_statuses))

    # Day-by-day breakdown
    daily = []
    activities_by_day = {}
    for a in activities:
        d = a.start_date.date()
        activities_by_day.setdefault(d, []).append(a)
    for i in range(7):
        d = (week_start + timedelta(days=i)).date()
        day_acts = activities_by_day.get(d, [])
        if not day_acts:
            summary = 'Rest'
        else:
            cats = Counter(_categorize(a.type).capitalize() for a in day_acts)
            summary = ' · '.join(f"{n} {c}" for c, n in cats.items())
        daily.append({
            'day': DAY_NAMES[i],
            'date': d.isoformat(),
            'activities': len(day_acts),
            'total_duration_min': round(sum(a.duration_s or 0 for a in day_acts) / 60),
            'summary': summary,
        })

    # Highlight: pick the most notable activity
    highlight = None
    if buckets['running']:
        longest = max(buckets['running'], key=lambda a: a.distance_m or 0)
        if (longest.distance_m or 0) > 0:
            day = DAY_NAMES[longest.start_date.weekday()]
            mins = (longest.duration_s or 0) // 60
            highlight = f"Longest run: {(longest.distance_m or 0)/1000:.1f} km in {mins // 60}h {mins % 60:02d}m on {day}"
    elif buckets['walking']:
        longest = max(buckets['walking'], key=lambda a: a.distance_m or 0)
        if (longest.distance_m or 0) > 0:
            day = DAY_NAMES[longest.start_date.weekday()]
            highlight = f"Longest walk: {(longest.distance_m or 0)/1000:.1f} km on {day}"
    elif buckets['strength']:
        longest = max(buckets['strength'], key=lambda a: a.duration_s or 0)
        if (longest.duration_s or 0) > 0:
            day = DAY_NAMES[longest.start_date.weekday()]
            highlight = f"Longest strength session: {(longest.duration_s or 0) // 60} min on {day}"

    # Personal records broken this week
    pr_result = await db.execute(
        select(PersonalRecord)
        .where(PersonalRecord.user_id == user_id)
        .where(PersonalRecord.achieved_at >= week_start)
        .where(PersonalRecord.achieved_at < week_end)
    )
    prs = [{
        'metric': pr.metric,
        'value': pr.value,
        'achieved_at': pr.achieved_at.isoformat(),
    } for pr in pr_result.scalars()]

    # Totals
    active_days = len(activities_by_day)
    total_calories = sum(a.calories or 0 for a in activities)
    total_duration = sum(a.duration_s or 0 for a in activities)

    label = f"{week_start.strftime('%b %-d')} – {(week_end - timedelta(days=1)).strftime('%-d, %Y')}"

    return {
        "week_label": label,
        "week_start": week_start.date().isoformat(),
        "week_end": (week_end - timedelta(days=1)).date().isoformat(),
        "week_offset": week_offset,
        "highlight": highlight,
        "running": _distance_stats(buckets['running']),
        "walking": _distance_stats(buckets['walking']),
        "cycling": _distance_stats(buckets['cycling']),
        "strength": _duration_stats(buckets['strength']),
        "swimming": _distance_stats(buckets['swimming']),
        "sleep": {
            "avg_score": avg_sleep,
            "nights_tracked": len(sleep_scores),
        },
        "recovery": {
            "avg_body_battery": avg_battery,
            "hrv_breakdown": hrv_counts,
            "days_tracked": len(hrv_statuses),
        },
        "daily": daily,
        "prs": prs,
        "totals": {
            "active_days": active_days,
            "total_activities": len(activities),
            "total_calories": total_calories,
            "total_duration_min": round(total_duration / 60),
        },
    }


def _delta(curr, prev):
    if curr is None or prev is None:
        return None
    return round(curr - prev, 1) if isinstance(curr, float) else curr - prev


def _pct(curr, prev):
    if not prev:
        return None
    return round((curr - prev) / prev * 100)


@router.get("/weekly-report")
async def weekly_report(
    user_id: uuid.UUID,
    week_offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    current = await _compute_week_stats(user_id, week_offset, db)
    previous = await _compute_week_stats(user_id, week_offset - 1, db)

    current['comparison'] = {
        'running_km_delta': _delta(current['running']['total_km'], previous['running']['total_km']),
        'running_km_pct': _pct(current['running']['total_km'], previous['running']['total_km']),
        'running_sessions_delta': current['running']['sessions'] - previous['running']['sessions'],
        'walking_km_delta': _delta(current['walking']['total_km'], previous['walking']['total_km']),
        'strength_sessions_delta': current['strength']['sessions'] - previous['strength']['sessions'],
        'active_days_delta': current['totals']['active_days'] - previous['totals']['active_days'],
        'total_activities_delta': current['totals']['total_activities'] - previous['totals']['total_activities'],
        'total_calories_delta': current['totals']['total_calories'] - previous['totals']['total_calories'],
        'sleep_score_delta': _delta(current['sleep']['avg_score'], previous['sleep']['avg_score']),
        'body_battery_delta': _delta(current['recovery']['avg_body_battery'], previous['recovery']['avg_body_battery']),
    }
    return current
