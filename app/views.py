"""The other views in the scout-report style (D63): ranked list, compare cards, fixture grid.

Built from the same CSS and parts as the scout card (components.py).
"""
from html import escape

from components import CSS, SECTIONS, fmt, ramp, team_badge

PAGE_CSS = """
<style>
html, body, [class*="css"] { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
.block-container { padding-top: 2rem; max-width: 1280px; }
h1 { font-weight: 800; letter-spacing: -.02em; }
/* Bordered containers become cards, matching the scout card. */
div[data-testid="stVerticalBlockBorderWrapper"] {
  background: #fcfcfb; border-color: rgba(11,11,11,0.10) !important; border-radius: 14px !important; }
button[data-baseweb="tab"] p { font-size: 15px; font-weight: 600; }
.fs-h { color: #898781; font-size: 11px; font-weight: 600; letter-spacing: .08em;
  text-transform: uppercase; margin: 2px 0 8px; }
</style>
"""

LIST_CSS = """
<style>
.fs-list { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; color: #0b0b0b; }
.fs-li { display: grid; grid-template-columns: 30px 38px minmax(160px, 1.5fr) 52px 44px 54px 50px 92px
  repeat(3, minmax(96px, 1fr)) 60px; align-items: center; gap: 12px; padding: 8px 12px;
  border-bottom: 1px solid #f0efec; font-size: 13px; }
.fs-li.hd { color: #898781; font-size: 11px; font-weight: 600; letter-spacing: .06em;
  text-transform: uppercase; border-bottom: 1px solid #e1e0d9; }
.fs-li .rk { color: #898781; font-variant-numeric: tabular-nums; text-align: right; }
.fs-li .bd { width: 34px; height: 34px; border-radius: 50%; background: #0b0b0b; color: #fff;
  display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: 700; }
.fs-li .nm { font-weight: 650; }
.fs-li .sub { color: #898781; font-size: 12px; }
.fs-li .num { font-variant-numeric: tabular-nums; text-align: right; font-weight: 600; }
.fs-mini { display: flex; align-items: center; gap: 6px; }
.fs-mini .fs-track { flex: 1; height: 6px; }
.fs-mini span { width: 24px; text-align: right; color: #52514e; font-size: 12px;
  font-variant-numeric: tabular-nums; }
.fs-pill { display: inline-block; margin-left: 6px; padding: 0 7px; border-radius: 10px;
  font-size: 11px; border: 1px solid #c3c2b7; color: #52514e; font-weight: 400; }
</style>
"""


def section_title(text):
    return f'<div class="fs-h">{escape(text)}</div>'


def sparkline(values, width=88, height=26):
    """Inline SVG bars of points in the last 8 matches played; the latest bar is blue."""
    vals = [v for v in (list(values) if values is not None else []) if v == v][-8:]
    if not vals:
        return ""
    top = max(max(vals), 10)
    bw = width / 8
    bars = []
    for i, v in enumerate(vals):
        h = max(max(v, 0) / top * (height - 2), 1.5)
        colour = "#2a78d6" if i == len(vals) - 1 else "#c3c2b7"
        x = (8 - len(vals) + i) * bw
        bars.append(f'<rect x="{x + 1:.1f}" y="{height - h:.1f}" width="{bw - 2:.1f}" '
                    f'height="{h:.1f}" rx="1.5" fill="{colour}"><title>{v:.0f} pts</title></rect>')
    return (f'<svg width="{width}" height="{height}" role="img" '
            f'aria-label="Points in last {len(vals)} matches">{"".join(bars)}</svg>')


def mini_bar(pct, value=None, spec=".2f"):
    """Percentile bar with the raw value beside it (D81: values, not only percentiles)."""
    if pct is None or pct != pct:
        return '<div class="fs-mini"><span style="width:auto;color:#898781">–</span></div>'
    val = "" if value is None or value != value else format(value, spec)
    return (f'<div class="fs-mini" title="{pct:.0f}th percentile"><span style="width:34px;'
            f'text-align:left;color:#0b0b0b;font-weight:600">{val}</span>'
            f'<div class="fs-track"><div class="fs-fill" style="width:{pct:.0f}%"></div></div>'
            f'<span>{pct:.0f}</span></div>')


def ranked_list(rows, pctl_cols):
    """rows: dicts already sorted. pctl_cols: three (label, percentile column) mini bars."""
    out = [CSS, LIST_CSS, '<div class="fs-card" style="padding:6px 6px"><div class="fs-list">',
           '<div class="fs-li hd"><div class="rk">#</div><div></div><div>Player</div>'
           '<div class="num" title="Expected points next GW / next 5">xP</div><div class="num">Pts</div><div class="num">Pts/mt</div><div class="num">Pts/£m</div><div>Last 8</div>'
           + "".join(f"<div>{escape(l)}</div>" for l, _, _ in pctl_cols)
           + '<div class="num">Owned</div></div>']
    for i, p in enumerate(rows, 1):
        pill = "" if p["status"] == "a" else f'<span class="fs-pill">{escape(p["status"])}</span>'
        pen = p.get("penalties_order")
        if pen is not None and pen == pen and int(pen) == 1:
            pill += '<span class="fs-pill" title="First-choice penalty taker">pens</span>'
        out.append(
            f'<div class="fs-li" title="{escape(p.get("news") or "")}">'
            f'<div class="rk">{i}</div><div>{team_badge(p["current_team"], p.get("team_primary_colour"), p.get("team_secondary_colour"), 34, 10)}</div>'
            f'<div><div class="nm">{escape(p["web_name"])}{pill}</div>'
            f'<div class="sub">{escape(p["current_team"])} · {p["position_code"]} · '
            f'£{p["price"]:.1f}m</div></div>'
            f'<div class="num" title="next 5 GWs: {fmt(p.get("xpts_next5"), ".1f")}">'
            f'{fmt(p.get("xpts_next_gw"), ".1f")}</div>'
            f'<div class="num">{fmt(p.get("total_points_season"), ".0f")}</div>'
            f'<div class="num">{fmt(p.get("points_per_match_season"), ".2f")}</div>'
            f'<div class="num">{fmt(p.get("points_per_million"), ".1f")}</div>'
            f'<div>{sparkline(p.get("recent"))}</div>'
            + "".join(f"<div>{mini_bar(p.get(c), p.get(c.removesuffix('_pctl')), spec)}</div>"
                      for _, c, spec in pctl_cols)
            + f'<div class="num">{fmt(p.get("selected_by_pct"), ".1f")}%</div></div>')
    if not rows:
        out.append('<div class="fs-na" style="padding:16px">No players match these filters.</div>')
    out.append("</div></div>")
    return "".join(out)


def compare_card(p, colour, pool_size):
    """Compact card per player; the top stripe is his series colour in the chart below."""
    pos = p["position_code"]
    eligible = p.get("is_rank_eligible")
    rows = []
    for _, label, pcol, rcol, rfmt, positions in SECTIONS:
        if positions and pos not in positions:
            continue
        pct = p.get(pcol) if eligible else None
        width = 0 if pct is None or pct != pct else pct
        rows.append(
            f'<div class="fs-row" style="grid-template-columns: 1fr 64px 28px" '
            f'title="{escape(label)}: {fmt(p.get(rcol), rfmt)}">'
            f'<div class="n">{escape(label)}<br><span class="raw">{fmt(p.get(rcol), rfmt)}</span></div>'
            f'<div class="fs-track"><div class="fs-fill" style="width:{width:.0f}%"></div></div>'
            f'<div class="p">{fmt(pct, ".0f")}</div></div>')
    rank = p.get("points_per_match_season_rank")
    rank_txt = f" · #{int(rank)} of {pool_size}" if rank == rank and rank else ""
    note = "" if eligible else ('<div class="fs-na" style="margin-top:8px">Too few minutes for '
                                'percentiles.</div>')
    return (CSS + f'<div class="fs-card" style="border-top:4px solid {colour};padding:16px">'
            '<div style="display:flex;align-items:center;gap:10px">'
            + team_badge(p["current_team"], p.get("team_primary_colour"),
                         p.get("team_secondary_colour"), 38, 11)
            + f'<div><div class="fs-name" style="font-size:18px">{escape(p["web_name"])}</div>'
            f'<div class="fs-meta">{escape(p["current_team"])} · {pos} · £{p["price"]:.1f}m</div></div></div>'
            '<div class="fs-tiles" style="grid-template-columns:1fr 1fr 1fr;margin:12px 0 4px">'
            f'<div class="fs-tile"><div class="v" style="font-size:20px">'
            f'{fmt(p.get("total_points_season"), ".0f")}</div><div class="l">Points</div></div>'
            f'<div class="fs-tile"><div class="v" style="font-size:20px">'
            f'{fmt(p.get("points_per_match_season"), ".2f")}</div>'
            f'<div class="l">PPM{rank_txt}</div></div>'
            f'<div class="fs-tile"><div class="v" style="font-size:20px">'
            f'{fmt(p.get("xpts_next_gw"), ".1f")}</div><div class="l">xP next GW</div></div></div>'
            + note + "".join(rows) + "</div>")


def fixture_grid(fx, view, colours=None):
    """Team rows x next-5 GW chips, shaded like the scout card's chips.
    view 'attack': expected goals scored (attackers). 'defence': clean-sheet chance."""
    attack = view == "attack"
    key = "expected_goals_for" if attack else "clean_sheet_prob"
    lo, hi = (0.8, 2.2) if attack else (0.1, 0.5)
    gws = sorted(fx.gameweek.unique())
    teams = fx.groupby("team")[key].mean().sort_values(ascending=False).index.tolist()
    cols = f"grid-template-columns: 56px repeat({len(gws)}, 1fr) 56px"
    out = [CSS, '<div class="fs-card" style="padding:14px 16px">',
           f'<div class="fs-row" style="{cols};color:#898781;font-size:11px;font-weight:600;'
           'letter-spacing:.06em"><div>TEAM</div>'
           + "".join(f"<div>GW{int(g)}</div>" for g in gws)
           + '<div style="text-align:right">AVG</div></div>']
    for t in teams:
        tf = fx[fx.team == t]
        cells = []
        for g in gws:
            games = tf[tf.gameweek == g].sort_values("kickoff_at").to_dict("records")
            if not games:
                cells.append('<div class="fs-chip" style="background:#f0efec;color:#898781;'
                             'padding:6px 8px">Blank</div>')
                continue
            inner = []
            for f in games:
                bg, ink = ramp(f[key], lo, hi)
                val = f"{f[key]:.1f} xG" if attack else f"{f[key]:.0%} CS"
                inner.append(
                    f'<div class="fs-chip" style="background:{bg};color:{ink};padding:6px 8px" '
                    f'title="{escape(t)} v {escape(f["opponent"])} '
                    f'({"home" if f["is_home"] else "away"}): '
                    f'{f["expected_goals_for"]:.2f} expected goals for, '
                    f'{f["expected_goals_against"]:.2f} against, '
                    f'{f["clean_sheet_prob"]:.0%} clean-sheet chance">'
                    f'<span class="opp" style="font-size:13px">{escape(f["opponent"])} '
                    f'{"H" if f["is_home"] else "A"}</span> · {val}</div>')
            cells.append(f'<div style="display:grid;gap:3px">{"".join(inner)}</div>')
        avg = tf[key].mean()
        out.append(
            f'<div class="fs-row" style="{cols};align-items:stretch;margin:3px 0">'
            f'<div style="display:flex;align-items:center">'
            + team_badge(t, *(colours or {}).get(t, (None, None)), 36, 10) + '</div>'
            + "".join(cells)
            + '<div class="p" style="display:flex;align-items:center;justify-content:flex-end">'
            + (f"{avg:.2f}" if attack else f"{avg:.0%}") + "</div></div>")
    out.append('<div class="fs-foot">Sorted by the average over the next 5 GWs. A double '
               'gameweek stacks both fixtures in one cell. Hover a chip for both sides.</div>')
    out.append("</div>")
    return "".join(out)
