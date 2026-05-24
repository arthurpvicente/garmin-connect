import logging
import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)


async def send_message(text: str) -> bool:
    """Send a WhatsApp message via Twilio. Returns success."""
    sid = settings.twilio_account_sid
    token = settings.twilio_auth_token
    sender = settings.twilio_whatsapp_from
    recipient = settings.twilio_whatsapp_to

    if not all([sid, token, sender, recipient]):
        logger.warning("Twilio WhatsApp not configured — skipping send")
        return False

    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                url,
                auth=(sid, token),
                data={"From": sender, "To": recipient, "Body": text},
            )
        if resp.status_code not in (200, 201):
            logger.error("Twilio send failed (%s): %s", resp.status_code, resp.text)
            return False
        logger.info("WhatsApp message sent via Twilio")
        return True
    except Exception as e:
        logger.exception("Twilio send error: %s", e)
        return False


def _sign(n):
    if n is None or n == 0:
        return None
    return f"+{n}" if n > 0 else f"{n}"


def _activity_block(emoji: str, label: str, data: dict, show_pace: bool = True) -> list[str]:
    sessions = data.get("sessions", 0)
    if sessions == 0:
        return []
    km = data.get("total_km", 0)
    header = f"{emoji} *{label}*  ({sessions} session{'s' if sessions != 1 else ''}"
    if km > 0:
        header += f" · {km} km"
    header += ")"
    lines = [header]
    if show_pace and data.get("avg_pace_fmt"):
        lines.append(f"• Avg pace {data['avg_pace_fmt']}/km")
    if data.get("total_elevation_m"):
        lines.append(f"• Elevation {data['total_elevation_m']} m")
    if data.get("longest_km") and data["longest_km"] > 0 and sessions > 1:
        lines.append(f"• Longest: {data['longest_km']} km")
    mins = data.get("total_duration_min", 0)
    if mins > 0:
        h, m = divmod(mins, 60)
        time_str = f"{h}h {m:02d}m" if h else f"{m} min"
        lines.append(f"• Total time {time_str}")
    return lines


def format_weekly_report(r: dict) -> str:
    """Format the weekly-report JSON as a rich WhatsApp message."""
    run = r.get("running", {}) or {}
    walk = r.get("walking", {}) or {}
    bike = r.get("cycling", {}) or {}
    swim = r.get("swimming", {}) or {}
    strength = r.get("strength", {}) or {}
    sleep = r.get("sleep", {}) or {}
    rec = r.get("recovery", {}) or {}
    tot = r.get("totals", {}) or {}
    cmp = r.get("comparison", {}) or {}
    daily = r.get("daily", []) or []
    prs = r.get("prs", []) or []
    highlight = r.get("highlight")
    label = r.get("week_label", "")

    lines = [f"🏃‍♂ *Weekly Report — {label}*", "─────────────"]

    # Highlight
    if highlight:
        lines += ["", "✨ *Highlight*", highlight]

    # Week vs last
    cmp_lines = []
    s = _sign(cmp.get("total_activities_delta"))
    if s is not None:
        cmp_lines.append(f"• Sessions {tot.get('total_activities', 0)} ({s})")
    s = _sign(cmp.get("active_days_delta"))
    if s is not None:
        cmp_lines.append(f"• Active days {tot.get('active_days', 0)}/7 ({s})")
    s = _sign(cmp.get("total_calories_delta"))
    if s is not None:
        cmp_lines.append(f"• Calories {tot.get('total_calories', 0):,} kcal ({s})")
    s = _sign(cmp.get("running_km_delta"))
    if s is not None and run.get("sessions", 0) > 0:
        cmp_lines.append(f"• Running {run.get('total_km', 0)} km ({s})")
    if cmp_lines:
        lines += ["", "📊 *This week vs last*"] + cmp_lines

    # Per-activity sections (only if there's data)
    for emoji, label_, data, show_pace in [
        ("🏃", "Running", run, True),
        ("🚶", "Walking", walk, False),
        ("🚴", "Cycling", bike, False),
        ("🏊", "Swimming", swim, False),
    ]:
        block = _activity_block(emoji, label_, data, show_pace=show_pace)
        if block:
            lines += [""] + block

    # Strength (duration-based)
    if strength.get("sessions", 0) > 0:
        mins = strength.get("total_duration_min", 0)
        h, m = divmod(mins, 60)
        time_str = f"{h}h {m:02d}m" if h else f"{m} min"
        lines += [
            "",
            f"🏋 *Strength*  ({strength['sessions']} session{'s' if strength['sessions'] != 1 else ''} · {time_str})",
        ]

    # Sleep
    if sleep.get("nights_tracked", 0) > 0:
        lines += ["", f"😴 *Sleep*  • Avg {sleep['avg_score']}/100  • {sleep['nights_tracked']} nights"]

    # Recovery
    if rec.get("days_tracked", 0) > 0:
        rec_parts = []
        if rec.get("avg_body_battery") is not None:
            rec_parts.append(f"Body battery {rec['avg_body_battery']}")
        if rec.get("hrv_breakdown"):
            hrv_str = ", ".join(f"{k} ×{v}" for k, v in rec["hrv_breakdown"].items())
            rec_parts.append(f"HRV: {hrv_str}")
        if rec_parts:
            lines += ["", f"💚 *Recovery*  • " + "  • ".join(rec_parts)]

    # PRs
    if prs:
        lines += ["", "🏆 *PRs broken this week*"]
        pr_labels = {
            "longest_run": lambda v: f"Longest run ({v/1000:.1f} km)",
            "highest_elevation": lambda v: f"Highest elevation ({int(v)} m)",
            "longest_duration": lambda v: f"Longest duration ({int(v // 60)} min)",
            "best_avg_pace": lambda v: f"Best pace ({int(abs(v) // 60)}'{int(abs(v) % 60):02d}\"/km)",
        }
        for pr in prs:
            fn = pr_labels.get(pr["metric"], lambda v: f"{pr['metric']}: {v}")
            lines.append(f"• {fn(pr['value'])}")

    # Day-by-day
    if daily:
        lines += ["", "📅 *Day-by-day*"]
        for d in daily:
            if d["activities"] == 0:
                lines.append(f"{d['day']}: Rest")
            else:
                lines.append(f"{d['day']}: {d['summary']} · {d['total_duration_min']} min")

    # Totals footer
    lines += [
        "",
        f"📊 {tot.get('total_activities', 0)} activities · {tot.get('active_days', 0)}/7 active days · {tot.get('total_calories', 0):,} kcal · {tot.get('total_duration_min', 0)} min",
    ]

    return "\n".join(lines)
