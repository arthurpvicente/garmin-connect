from fpdf import FPDF

# Accent colour (r, g, b)
RED   = (255, 68, 35)
DARK  = (30, 30, 30)
GREY  = (100, 100, 100)
LIGHT = (245, 245, 245)
WHITE = (255, 255, 255)
LINE  = (220, 220, 220)


class _PDF(FPDF):
    def header(self):
        pass  # custom header drawn in generate_weekly_pdf

    def _label(self, text: str):
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(*GREY)
        self.cell(0, 5, text.upper(), ln=True)
        self.set_text_color(*DARK)

    def _big(self, text: str, unit: str = ""):
        self.set_font("Helvetica", "B", 28)
        self.set_text_color(*RED)
        self.cell(self.get_string_width(text) + 2, 10, text)
        if unit:
            self.set_font("Helvetica", "", 11)
            self.set_text_color(*GREY)
            self.cell(0, 10, f" {unit}", ln=True)
        else:
            self.ln(10)
        self.set_text_color(*DARK)

    def _row(self, label: str, value: str):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(*GREY)
        self.cell(60, 7, label)
        self.set_text_color(*DARK)
        self.set_font("Helvetica", "B", 10)
        self.cell(0, 7, value, ln=True)

    def _section_card(self, x, y, w, h, icon: str, title: str, content_fn):
        self.set_xy(x, y)
        self.set_fill_color(*LIGHT)
        self.rect(x, y, w, h, "F")
        # icon + title
        self.set_xy(x + 6, y + 6)
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(*DARK)
        self.cell(0, 7, title if not icon else f"{icon}  {title}", ln=True)
        # divider
        self.set_draw_color(*LINE)
        self.line(x + 6, y + 15, x + w - 6, y + 15)
        self.set_xy(x + 6, y + 18)
        content_fn()


def _safe(text: str) -> str:
    """Replace non-latin-1 characters so built-in Helvetica doesn't choke."""
    return (text
        .replace("–", "-").replace("—", "-")
        .replace("‘", "'").replace("’", "'")
        .encode("latin-1", errors="replace").decode("latin-1"))


def generate_weekly_pdf(report: dict) -> bytes:
    r = report.get("running", {})
    g = report.get("gym", {})
    s = report.get("sleep", {})
    rec = report.get("recovery", {})
    t = report.get("totals", {})

    pdf = _PDF()
    pdf.add_page()
    pdf.set_auto_page_break(False)

    # ── Header ────────────────────────────────────────────────────────────
    pdf.set_fill_color(*DARK)
    pdf.rect(0, 0, 210, 28, "F")
    pdf.set_xy(10, 7)
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(*WHITE)
    pdf.cell(0, 8, "Weekly Fitness Report", ln=True)
    pdf.set_xy(10, 17)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*RED)
    pdf.cell(0, 5, _safe(report.get("week_label", "")))
    pdf.set_text_color(*DARK)

    # ── Running card (left, top) ──────────────────────────────────────────
    def running_content():
        if r.get("sessions", 0) == 0:
            pdf.set_font("Helvetica", "I", 10)
            pdf.set_text_color(*GREY)
            pdf.cell(0, 7, "No runs this week.", ln=True)
            pdf.set_text_color(*DARK)
            return
        pdf._big(str(r.get("total_km", 0)), "km")
        pdf._row("Sessions",  str(r.get("sessions", 0)))
        pdf._row("Avg pace",  r.get("avg_pace_fmt") or "—")
        pdf._row("Elevation", f"{r.get('total_elevation_m', 0)} m")
        pdf._row("Longest",   f"{r.get('longest_run_km', 0)} km")

    pdf._section_card(10, 32, 90, 72, "", "Running", running_content)

    # ── Gym card (right, top) ─────────────────────────────────────────────
    def gym_content():
        if g.get("sessions", 0) == 0:
            pdf.set_font("Helvetica", "I", 10)
            pdf.set_text_color(*GREY)
            pdf.cell(0, 7, "No gym sessions this week.", ln=True)
            pdf.set_text_color(*DARK)
            return
        pdf._big(str(g.get("sessions", 0)), "sessions")
        pdf._row("Total time", f"{g.get('total_duration_min', 0)} min")

    pdf._section_card(110, 32, 90, 72, "", "Gym & Strength", gym_content)

    # ── Sleep card (left, bottom) ─────────────────────────────────────────
    def sleep_content():
        score = s.get("avg_score")
        nights = s.get("nights_tracked", 0)
        if score is None or nights == 0:
            pdf.set_font("Helvetica", "I", 10)
            pdf.set_text_color(*GREY)
            pdf.cell(0, 7, "No Garmin sleep data.", ln=True)
            pdf.set_text_color(*DARK)
            return
        pdf._big(str(score), "/ 100")
        pdf._row("Nights tracked", str(nights))
        quality = "Good" if score >= 75 else ("Fair" if score >= 55 else "Poor")
        pdf._row("Quality", quality)

    pdf._section_card(10, 112, 90, 60, "", "Sleep Quality", sleep_content)

    # ── Recovery card (right, bottom) ────────────────────────────────────
    def recovery_content():
        battery = rec.get("avg_body_battery")
        hrv = rec.get("hrv_breakdown", {})
        days = rec.get("days_tracked", 0)
        if battery is None and days == 0:
            pdf.set_font("Helvetica", "I", 10)
            pdf.set_text_color(*GREY)
            pdf.cell(0, 7, "No Garmin recovery data.", ln=True)
            pdf.set_text_color(*DARK)
            return
        if battery is not None:
            pdf._big(str(battery), "/ 100")
        if hrv:
            pdf._row("HRV", "  ".join(f"{k}: {v}" for k, v in hrv.items()))

    pdf._section_card(110, 112, 90, 60, "", "Recovery & HRV", recovery_content)

    # ── Summary bar ───────────────────────────────────────────────────────
    y_sum = 182
    pdf.set_fill_color(*DARK)
    pdf.rect(10, y_sum, 190, 22, "F")

    cells = [
        ("Active days", f"{t.get('active_days', 0)} / 7"),
        ("Activities",  str(t.get("total_activities", 0))),
        ("Calories",    f"{t.get('total_calories', 0):,} kcal"),
        ("Total time",  f"{t.get('total_duration_min', 0)} min"),
    ]
    col_w = 190 / len(cells)
    for i, (label, value) in enumerate(cells):
        cx = 10 + i * col_w
        pdf.set_xy(cx + 5, y_sum + 4)
        pdf.set_font("Helvetica", "", 7)
        pdf.set_text_color(*GREY)
        pdf.cell(col_w - 5, 4, label.upper(), ln=True)
        pdf.set_xy(cx + 5, y_sum + 10)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(*WHITE)
        pdf.cell(col_w - 5, 6, value)

    # ── Footer ────────────────────────────────────────────────────────────
    pdf.set_xy(10, 208)
    pdf.set_font("Helvetica", "", 7)
    pdf.set_text_color(*GREY)
    pdf.cell(0, 5, "Generated by Fitness Sync · fitness-sync.local")

    return bytes(pdf.output())
