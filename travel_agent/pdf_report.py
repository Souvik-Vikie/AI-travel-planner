"""Render the final trip plan as a PDF (fpdf2, core fonts -> latin-1 only, so text is sanitised)."""
import datetime as dt
import os

from fpdf import FPDF

from .config import settings
from .data import get_destination

ACCENT = (20, 90, 140)


def _t(text) -> str:
    return str(text).replace("→", "->").replace("–", "-").replace("—", "-") \
        .replace("’", "'").encode("latin-1", "replace").decode("latin-1")


class _PDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(130)
        self.cell(0, 6, "AI Travel Planner - generated trip plan", align="R")
        self.ln(8)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(130)
        self.cell(0, 6, f"Page {self.page_no()}", align="C")

    def section(self, text):
        self.ln(3)
        self.set_font("Helvetica", "B", 13)
        self.set_text_color(*ACCENT)
        self.cell(0, 8, _t(text), new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*ACCENT)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(2)
        self.set_text_color(30)

    def para(self, text, bold=False, size=10):
        self.set_font("Helvetica", "B" if bold else "", size)
        self.multi_cell(0, 5.5, _t(text), new_x="LMARGIN", new_y="NEXT")

    def kv(self, label, value, widths=(55, 0)):
        self.set_font("Helvetica", "B", 10)
        self.cell(widths[0], 6.5, _t(label))
        self.set_font("Helvetica", "", 10)
        self.cell(widths[1], 6.5, _t(value), new_x="LMARGIN", new_y="NEXT")


def build_pdf(state) -> str:
    req, cost, f, h = state["request"], state["cost"], state["flight"], state["hotel"]
    d = get_destination(state["destination"]["id"])
    b = state.get("bookings", {})
    pdf = _PDF()
    pdf.set_auto_page_break(True, margin=15)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 22)
    pdf.set_text_color(*ACCENT)
    pdf.cell(0, 12, _t(f"Your trip to {d['name']}"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(80)
    pdf.cell(0, 7, _t(f"{d['country']}  |  {f['depart_date']} to {f['return_date']}  |  {req['travellers']} traveller(s)"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(30)

    pdf.section("Why this destination")
    pdf.para(state["selection_reason"])
    clim = next((c["climate"] for c in state["candidates"] if c["id"] == d["id"]), {})
    if "avg_max_c" in clim:
        pdf.para(f"Weather in {dt.date(2000, req['month'], 1):%B}: avg high {clim['avg_max_c']}C, low {clim['avg_min_c']}C, "
              f"rain on {clim['rainy_day_ratio']:.0%} of days ({clim['source']}).")
    best = ", ".join(dt.date(2000, m, 1).strftime("%b") for m in sorted(d["best"]))
    pdf.para(f"Best months to visit: {best}.  Season for your dates: {clim.get('label', 'n/a')}.  Visa: {d['visa']}.")

    pdf.section("Budget")
    for label, key in (("Flights (round trip, all)", "flights"), (f"Hotel ({cost['nights']} nights x {cost['rooms']} room)", "hotel"),
                       ("Food, transport, entry fees", "daily_expenses"), ("Contingency (5%)", "buffer")):
        pdf.kv(label, f"INR {cost[key]:,.0f}", (80, 0))
    pdf.kv("TOTAL", f"INR {cost['total']:,.0f}   (budget INR {req['budget']:,.0f}, "
                     f"{'within' if cost['total'] <= req['budget'] else 'OVER'} budget)", (80, 0))

    pdf.section("Bookings")
    fb, hb = b.get("flight", {}), b.get("hotel", {})
    pdf.kv("Flight", f"{f['airline']}, {f['stops']} stop(s), ~{f['duration_hours']}h each way")
    pdf.kv("Flight reference", f"{fb.get('reference', '-')}  ({fb.get('mode', '-')})")
    pdf.kv("Hotel", f"{h['name']}  ({h['rating']}/5, INR {h['price_per_night_inr']:,.0f}/night)")
    pdf.kv("Hotel reference", f"{hb.get('reference', '-')}  ({hb.get('mode', '-')})")
    fm = state["flight_meta"]
    if fm.get("chosen_offset"):
        pdf.para(f"Note: departure shifted {fm['chosen_offset']:+d} day(s) from your preferred date to save "
              f"INR {fm['flex_saving']:,.0f} on flights.")

    pdf.section("Day-by-day plan")
    for day in state["itinerary"]:
        pdf.para(f"Day {day['day']}  ({day['date']}) - {day['title']}", bold=True, size=11)
        for part in ("morning", "afternoon", "evening"):
            if day.get(part):
                pdf.para(f"   {part.capitalize()}: {day[part]}")
        pdf.ln(1)

    pdf.section("How the agent decided")
    for line in state.get("log", []):
        pdf.para(f"- {line}", size=8.5)

    os.makedirs(settings.output_dir, exist_ok=True)
    path = os.path.join(settings.output_dir, f"trip_{d['id']}_{f['depart_date']}.pdf")
    pdf.output(path)
    return path
