"""Hand-built SVG charts in the scout-card style (D67).

Rules from the dataviz method: thin marks (2px lines, rounded bar ends), no legend box (end
labels name each line, text in ink tokens, the series colour sits only on the mark), recessive
axes with no gridlines, one y-axis per chart, and a hover title on every mark or gameweek
column. Charts scale to their container through the viewBox.
"""
from html import escape

INK, INK2, MUTED, AXIS, SURFACE = "#0b0b0b", "#52514e", "#898781", "#c3c2b7", "#fcfcfb"
TRACK = "#f0efec"
BLUE, RED = "#2a78d6", "#e34948"

CHART_CSS = """
<style>
.fs-svg { width: 100%; height: auto; display: block; overflow: visible;
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
.fs-svg text { fill: #898781; font-size: 11px; }
.fs-svg .lbl { fill: #52514e; font-size: 12px; font-weight: 600; }
.fs-svg .val { fill: #0b0b0b; font-size: 11px; font-weight: 600; }
.fs-svg .hov { fill: transparent; }
.fs-svg .hov:hover { fill: rgba(11,11,11,0.05); }
.fs-svg .bar:hover { opacity: .8; }
.fs-note { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; color: #52514e;
  font-size: 12px; margin-top: 6px; }
.fs-note b { color: #0b0b0b; }
</style>
"""

W = 1100         # default viewBox width (full-width card); half-width charts pass ~520
# so that 1 viewBox unit is about 1 screen pixel and text stays 11-12px at either size.
PAD_L, PAD_R, PAD_T, PAD_B = 36, 110, 14, 26


def _f(v, fmt):
    return "–" if v is None or v != v else format(v, fmt)


def _nice_max(v):
    if v <= 0:
        return 1
    for step in (0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10, 20, 25, 50, 100):
        if v / step <= 4:
            return step * -(-v // step)
    return v


def _frame(height, y0, y1, y_fmt, xs, x_step_every=None, w=W):
    """Axis baseline, 3 quiet y tick labels, sparse GW labels on x."""
    plot_w = w - PAD_L - PAD_R
    plot_h = height - PAD_T - PAD_B
    out = []
    # Quiet round ticks: 0, half the (nice) top, the top; the bottom only when it's a real
    # negative range (diverging charts), so a stray -2 BPS doesn't become an axis label.
    ticks = [0, y1 / 2, y1] if y1 > 0 else [y1]
    if y0 < 0 and abs(y0) > 0.15 * max(abs(y1), 1e-9):
        ticks = [y0, 0, y1]
    for v in ticks:
        y = PAD_T + plot_h * (1 - (v - y0) / (y1 - y0))
        out.append(f'<text x="{PAD_L - 6}" y="{y + 4:.1f}" text-anchor="end">'
                   f'{_f(v, y_fmt.lstrip("+"))}</text>')
    base = PAD_T + plot_h * (1 - (0 - y0) / (y1 - y0)) if y0 < 0 < y1 else PAD_T + plot_h
    out.append(f'<line x1="{PAD_L}" x2="{w - PAD_R + 8}" y1="{base:.1f}" y2="{base:.1f}" '
               f'stroke="{AXIS}" stroke-width="1"/>')
    n = len(xs)
    every = x_step_every or max(1, round(n / 8))
    for i, x in enumerate(xs):
        if i % every == 0 or i == n - 1:
            cx = PAD_L + (plot_w * (i + 0.5) / n)
            out.append(f'<text x="{cx:.1f}" y="{height - 6}" text-anchor="middle">GW{x}</text>')
    return out, plot_w, plot_h


def line_chart(xs, series, height=220, y_fmt=".2f", y_min=0.0, y_max=None, ref=None, w=W):
    """xs: gameweek labels. series: dicts with name, values, colour, and optional raw (shown
    in the hover title), dashed, area. ref: optional (value, label) dotted reference line."""
    vals = [v for s in series for v in s["values"] if v is not None and v == v]
    if not vals:
        return '<div class="fs-note">No data yet.</div>'
    lo = min(y_min, min(vals)) if y_min is not None else min(vals)
    hi = _nice_max(max(vals + ([ref[0]] if ref else [])) * 1.05) if y_max is None else y_max
    out, plot_w, plot_h = _frame(height, lo, hi, y_fmt, xs, w=w)
    n = len(xs)

    def px(i):
        return PAD_L + plot_w * (i + 0.5) / n

    def py(v):
        return PAD_T + plot_h * (1 - (v - lo) / (hi - lo))

    if ref:
        y = py(ref[0])
        out.append(f'<line x1="{PAD_L}" x2="{w - PAD_R}" y1="{y:.1f}" y2="{y:.1f}" '
                   f'stroke="{AXIS}" stroke-dasharray="3 4"/>'
                   f'<text x="{w - PAD_R + 6}" y="{y + 4:.1f}">{escape(ref[1])}</text>')

    labels = []
    for s in series:
        pts = [(px(i), py(v)) for i, v in enumerate(s["values"]) if v is not None and v == v]
        if not pts:
            continue
        d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        if s.get("area"):
            out.append(f'<path d="{d} L{pts[-1][0]:.1f},{py(lo):.1f} L{pts[0][0]:.1f},{py(lo):.1f} Z" '
                       f'fill="{s["colour"]}" opacity="0.10"/>')
        dash = ' stroke-dasharray="5 4"' if s.get("dashed") else ""
        out.append(f'<path d="{d}" fill="none" stroke="{s["colour"]}" stroke-width="2" '
                   f'stroke-linejoin="round" stroke-linecap="round"{dash}/>')
        last_v = [v for v in s["values"] if v is not None and v == v][-1]
        x, y = pts[-1]
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{s["colour"]}" '
                   f'stroke="{SURFACE}" stroke-width="2"/>')
        labels.append([y, s, last_v, x])

    # End labels: value + name in ink, nudged apart so they never overlap.
    labels.sort(key=lambda t: t[0])
    for i in range(1, len(labels)):
        if labels[i][0] - labels[i - 1][0] < 15:
            labels[i][0] = labels[i - 1][0] + 15
    for y, s, v, x in labels:
        out.append(f'<circle cx="{w - PAD_R + 12}" cy="{y:.1f}" r="3.5" fill="{s["colour"]}"/>'
                   f'<text class="lbl" x="{w - PAD_R + 20}" y="{y + 4:.1f}">'
                   f'{_f(v, y_fmt)} <tspan style="font-weight:400;fill:#898781">'
                   f'{escape(s["name"])}</tspan></text>')

    # Hover column per gameweek with every series' value (and raw value) in the title.
    for i, x in enumerate(xs):
        lines = [f"GW{x}"]
        for s in series:
            v = s["values"][i]
            raw = s.get("raw")
            extra = f" (raw {_f(raw[i], y_fmt)})" if raw is not None else ""
            lines.append(f"{s['name']}: {_f(v, y_fmt)}{extra}")
        out.append(f'<rect class="hov" x="{PAD_L + plot_w * i / n:.1f}" y="{PAD_T}" '
                   f'width="{plot_w / n:.1f}" height="{plot_h}"><title>{escape(chr(10).join(lines))}'
                   f'</title></rect>')
    return (CHART_CSS + f'<svg class="fs-svg" viewBox="0 0 {w} {height}" role="img">'
            + "".join(out) + "</svg>")


def bar_chart(xs, values, height=160, fmt=".0f", colours=None, threshold=None, marks=None,
              titles=None, y_max=None, diverging=False, w=W):
    """Vertical bars, one per x. threshold: (value, label) line. marks: text above each bar.
    diverging: blue above zero, red below."""
    vals = [v for v in values if v is not None and v == v]
    if not vals:
        return '<div class="fs-note">No data yet.</div>'
    lo = min(0, min(vals))
    hi = y_max or _nice_max(max(max(vals), threshold[0] if threshold else 0) * 1.1)
    if diverging:
        m = max(abs(lo), abs(hi))
        lo, hi = -m, m
    out, plot_w, plot_h = _frame(height, lo, hi, fmt, xs, w=w)
    n = len(xs)
    bw = plot_w / n

    def py(v):
        return PAD_T + plot_h * (1 - (v - lo) / (hi - lo))

    zero = py(0)
    for i, v in enumerate(values):
        if v is None or v != v:
            continue
        colour = (colours[i] if colours else (BLUE if v >= 0 else RED) if diverging else BLUE)
        y = min(py(v), zero)
        h = max(abs(py(v) - zero), 1.5)
        x = PAD_L + bw * i + bw * 0.18
        title = titles[i] if titles else f"GW{xs[i]}: {_f(v, fmt)}"
        out.append(f'<rect class="bar" x="{x:.1f}" y="{y:.1f}" width="{bw * 0.64:.1f}" '
                   f'height="{h:.1f}" rx="3" fill="{colour}"><title>{escape(title)}</title></rect>')
        if marks and marks[i]:
            out.append(f'<text class="val" x="{x + bw * 0.32:.1f}" y="{y - 5:.1f}" '
                       f'text-anchor="middle">{escape(marks[i])}</text>')
    if threshold:
        y = py(threshold[0])
        out.append(f'<line x1="{PAD_L}" x2="{w - PAD_R}" y1="{y:.1f}" y2="{y:.1f}" '
                   f'stroke="{INK}" stroke-width="1.5" stroke-dasharray="4 3"/>'
                   f'<text class="lbl" x="{w - PAD_R + 6}" y="{y + 4:.1f}">{escape(threshold[1])}</text>')
    return (CHART_CSS + f'<svg class="fs-svg" viewBox="0 0 {w} {height}" role="img">'
            + "".join(out) + "</svg>")


def waterfall(items, total_label="Season total", w=W):
    """Horizontal waterfall: each category adds (blue) or removes (red) points, ending at the
    total (ink). items: (label, value) in display order."""
    items = [(l, v) for l, v in items if v]
    total = sum(v for _, v in items)
    running, lo, hi = 0, 0, 0
    for _, v in items:
        running += v
        lo, hi = min(lo, running), max(hi, running)
    hi = max(hi, total, 1)
    row_h, left, right = 26, 150, 60
    height = row_h * (len(items) + 1) + 12
    span = hi - lo

    def px(v):
        return left + (w - left - right) * (v - lo) / span

    out, running = [], 0
    for i, (label, v) in enumerate(items):
        start, end = running, running + v
        running = end
        y = 6 + i * row_h
        x0, x1 = px(min(start, end)), px(max(start, end))
        colour = BLUE if v >= 0 else RED
        out.append(f'<text class="lbl" x="{left - 10}" y="{y + 15}" text-anchor="end">{escape(label)}</text>'
                   f'<rect class="bar" x="{x0:.1f}" y="{y + 3}" width="{max(x1 - x0, 2):.1f}" height="{row_h - 8}" '
                   f'rx="3" fill="{colour}"><title>{escape(label)}: {v:+.0f} pts (running total '
                   f'{end:.0f})</title></rect>'
                   f'<text class="val" x="{x1 + 6:.1f}" y="{y + 15}">{v:+.0f}</text>')
        if i < len(items) - 1:
            out.append(f'<line x1="{px(end):.1f}" x2="{px(end):.1f}" y1="{y + row_h - 5}" '
                       f'y2="{y + row_h + 3}" stroke="{AXIS}"/>')
    y = 6 + len(items) * row_h
    out.append(f'<line x1="{left}" x2="{w - right}" y1="{y}" y2="{y}" stroke="{AXIS}"/>'
               f'<text class="lbl" x="{left - 10}" y="{y + 17}" text-anchor="end">{escape(total_label)}</text>'
               f'<rect x="{px(min(0, total)):.1f}" y="{y + 5}" width="{abs(px(total) - px(0)):.1f}" '
               f'height="{row_h - 8}" rx="3" fill="{INK}"><title>{total:.0f} pts</title></rect>'
               f'<text class="val" x="{px(total) + 6:.1f}" y="{y + 17}">{total:.0f}</text>')
    return (CHART_CSS + f'<svg class="fs-svg" viewBox="0 0 {w} {height + 14}" role="img">'
            + "".join(out) + "</svg>")


STRIP_RAMP = ["#f0efec", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95"]


def points_strip(matches):
    """One tile per fixture: points, shaded; the returns that produced them written underneath
    (G goal, A assist, CS clean sheet, B bonus, DC defensive contribution). 0 minutes: dash."""
    tiles = []
    for m in matches:
        pts = m["total_points"]
        if not m["minutes"]:
            bg, ink, txt, tags = "#f9f9f7", MUTED, "–", "did not play"
        else:
            i = min(len(STRIP_RAMP) - 1, max(0, round(pts / 2)))
            bg = STRIP_RAMP[i]
            ink = "#ffffff" if i >= 4 else INK
            txt = f"{pts:.0f}"
            tags = " ".join(filter(None, [
                "G" * int(m["goals_scored"]), "A" * int(m["assists"]),
                "CS" if m["clean_sheets"] else "", f"B{int(m['bonus'])}" if m["bonus"] else "",
                "DC" if m.get("defensive_contribution_points") else ""])) or "·"
        tiles.append(
            f'<div title="GW{m["gameweek"]} v {escape(m["opponent"])}: {txt} pts, '
            f'{m["minutes"]:.0f} min" style="flex:1;min-width:44px;text-align:center">'
            f'<div style="color:#898781;font-size:11px">GW{m["gameweek"]}</div>'
            f'<div style="background:{bg};color:{ink};border-radius:10px;padding:8px 2px;'
            f'font-weight:700;font-size:16px;margin:3px 0">{txt}</div>'
            f'<div style="color:#52514e;font-size:11px;white-space:nowrap">{escape(tags)}</div>'
            f'<div style="color:#898781;font-size:10px">{escape(m["opponent"])}</div></div>')
    return ('<div style="display:flex;gap:6px;font-family:system-ui,-apple-system,'
            '\'Segoe UI\',sans-serif;overflow-x:auto">' + "".join(tiles) + "</div>")


def bullets(rows, avg, avg_label, fmt=".2f", higher_is_better=True):
    """Horizontal bars per window against a position-average marker."""
    hi = _nice_max(max([v for _, v in rows if v == v] + [avg]) * 1.1)
    out = ['<div style="font-family:system-ui,-apple-system,\'Segoe UI\',sans-serif">']
    for label, v in rows:
        w = 0 if v != v else 100 * v / hi
        a = 100 * avg / hi
        better = (v >= avg) == higher_is_better
        out.append(
            f'<div style="display:grid;grid-template-columns:70px 1fr 52px;gap:10px;'
            f'align-items:center;margin:6px 0;font-size:13px" title="{escape(label)}: '
            f'{_f(v, fmt)} vs {escape(avg_label)} {_f(avg, fmt)}">'
            f'<div style="color:#52514e">{escape(label)}</div>'
            f'<div style="position:relative;height:14px;background:{TRACK};border-radius:7px">'
            f'<div style="width:{w:.1f}%;height:100%;border-radius:7px;background:{BLUE};'
            f'opacity:{1 if better else .45}"></div>'
            f'<div style="position:absolute;left:{a:.1f}%;top:-4px;width:2px;height:22px;'
            f'background:{INK}"></div></div>'
            f'<div style="text-align:right;font-weight:600;font-variant-numeric:tabular-nums">'
            f'{_f(v, fmt)}</div></div>')
    out.append(f'<div class="fs-note" style="font-size:11px;color:#898781">Black marker: '
               f'{escape(avg_label)} {_f(avg, fmt)}. Faded bar: below it.</div></div>')
    return CHART_CSS + "".join(out)
