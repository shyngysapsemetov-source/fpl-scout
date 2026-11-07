"""Hand-built HTML/CSS pieces for the scout report (D59, D60).

Colours are the validated reference palette (see app.py). Text always uses ink tokens, never
a series colour; percentile fills use the one sequential hue (blue).
"""
from html import escape

CSS = """
<style>
.fs-card { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; color: #0b0b0b;
  background: #fcfcfb; border: 1px solid rgba(11,11,11,0.10); border-radius: 14px;
  padding: 20px 22px; }
.fs-head { display: flex; align-items: center; gap: 14px; }
.fs-badge { width: 52px; height: 52px; border-radius: 50%; background: #0b0b0b; color: #fff;
  display: flex; align-items: center; justify-content: center; font-weight: 700;
  font-size: 13px; letter-spacing: .04em; flex: none; }
.fs-name { font-size: 24px; font-weight: 700; line-height: 1.1; }
.fs-meta { color: #52514e; font-size: 13px; margin-top: 3px; }
.fs-status { display: inline-block; margin-left: 8px; padding: 1px 8px; border-radius: 10px;
  font-size: 12px; border: 1px solid #c3c2b7; color: #52514e; }
.fs-tiles { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; margin: 18px 0 6px; }
.fs-tile { background: #f9f9f7; border-radius: 10px; padding: 12px 14px; }
.fs-tile .v { font-size: 26px; font-weight: 700; line-height: 1.1; }
.fs-tile .l { color: #52514e; font-size: 12px; margin-top: 4px; }
.fs-tile .s { color: #898781; font-size: 12px; }
.fs-sec { color: #898781; font-size: 11px; font-weight: 600; letter-spacing: .08em;
  text-transform: uppercase; margin: 16px 0 6px; }
.fs-row { display: grid; grid-template-columns: 150px 1fr 44px; align-items: center;
  gap: 10px; margin: 5px 0; font-size: 13px; }
.fs-row .n { color: #52514e; }
.fs-row .raw { color: #898781; font-size: 11px; }
.fs-track { height: 10px; background: #f0efec; border-radius: 5px; overflow: hidden; }
.fs-fill { height: 100%; border-radius: 5px; background: #2a78d6; }
.fs-row .p { text-align: right; font-variant-numeric: tabular-nums; font-weight: 600; }
.fs-na { color: #898781; font-size: 12px; }
.fs-fx { display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; }
.fs-chip { border-radius: 10px; padding: 8px 10px; font-size: 12px; line-height: 1.35; }
.fs-chip .gw { color: inherit; opacity: .75; font-size: 11px; }
.fs-chip .opp { font-weight: 700; font-size: 14px; }
.fs-foot { color: #898781; font-size: 11px; margin-top: 12px; }
</style>
"""

# Sequential blue, light -> dark (reference ramp); text flips to white on the dark half.
# Fixture difficulty, red (hard) -> yellow -> green (easy), his choice (D88), FPL-style.
# Red/green is the hardest pair for colour-blind readers, so every chip also prints its number
# (xG or clean-sheet %); colour never carries the meaning alone.
RAMP = ["#c62828", "#e4572e", "#f28e2b", "#f6bf4f", "#f7e27a", "#d4e483", "#a5d27a",
        "#6dbb6a", "#3b9b54", "#1f7a3e"]
DARK_ENDS = {0, 1, 8, 9}   # white text on the strongest reds and greens


def ramp(x, lo, hi):
    """Colour and text ink for value x on a lo..hi scale (higher = easier = greener)."""
    t = 0 if hi == lo else min(max((x - lo) / (hi - lo), 0), 1)
    i = round(t * (len(RAMP) - 1))
    return RAMP[i], ("#ffffff" if i in DARK_ENDS else "#0b0b0b")


def _luminance(hex_colour):
    h = hex_colour.lstrip("#")
    rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def team_badge(short_name, primary, secondary, size=52, font=13):
    """Round badge in club colours (D65). The secondary colour is a ring, which also keeps
    white kits (Fulham, Leeds) visible on the light surface."""
    primary = primary or "#0b0b0b"
    secondary = secondary or "#c3c2b7"
    lum = _luminance(primary)
    # White wherever it clears 3:1 (bold badge text); near-black only on light kits.
    ink = "#ffffff" if 1.05 / (lum + 0.05) >= 3 else "#0b0b0b"
    ring = secondary if secondary.lower() != primary.lower() else "#c3c2b7"
    return (f'<div style="width:{size}px;height:{size}px;border-radius:50%;background:{primary};'
            f'color:{ink};box-shadow:inset 0 0 0 {max(2, size // 16)}px {ring}, '
            f'0 0 0 1px rgba(11,11,11,0.10);display:flex;align-items:center;'
            f'justify-content:center;font-weight:700;font-size:{font}px;letter-spacing:.04em;'
            f'flex:none">{escape(short_name)}</div>')


def ordinal(n):
    n = int(n)
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def fmt(v, spec):
    return "–" if v is None or v != v else format(v, spec)


# Card sections (D61): (section, label, percentile column, raw column, raw format, positions)
SECTIONS = [
    ("Output", "Points per match", "points_per_match_season_pctl", "points_per_match_season", ".2f", None),
    ("Output", "Last-5 ppm", "points_per_match_l5_pctl", "points_per_match_l5", ".2f", None),
    ("Output", "Points per £m", "points_per_million_pctl", "points_per_million", ".1f", None),
    ("Output", "Expected pts, next 5 GWs", "xpts_next5_pctl", "xpts_next5", ".1f", None),
    ("Attack", "xG per 90", "xg_per90_season_pctl", "xg_per90_season", ".2f", None),
    ("Attack", "xA per 90", "xa_per90_season_pctl", "xa_per90_season", ".2f", None),
    ("Bonus", "BPS per 90", "bps_per90_season_pctl", "bps_per90_season", ".1f", None),
    ("Minutes", "Minutes per match", "minutes_per_match_season_pctl", "minutes_per_match_season", ".0f", None),
    ("Minutes", "Played, last 5", "played_share_l5_pctl", "played_share_l5", ".0%", None),
    ("Defence", "xGC per 90 (low is good)", "xgc_per90_season_pctl", "xgc_per90_season", ".2f", ("GKP", "DEF")),
    ("Defence", "Clean-sheet chance, next 5", "next5_clean_sheet_prob_pctl", "next5_clean_sheet_prob", ".0%", ("GKP", "DEF")),
    ("Defence", "Def. contribution per 90", "defensive_contribution_per90_season_pctl", "defensive_contribution_per90_season", ".1f", ("DEF", "MID", "FWD")),
    ("Defence", "Saves per 90", "saves_per90_season_pctl", "saves_per90_season", ".1f", ("GKP",)),
]


def set_piece_text(p):
    out = []
    for label, col in (("Penalties", "penalties_order"), ("Corners", "corners_order"),
                       ("Free kicks", "direct_freekicks_order")):
        v = p.get(col)
        if v is not None and v == v:
            out.append(f"{label} #{int(v)}")
    return " · ".join(out)


def role_line(p):
    """How he gets his minutes and points (D81): starts, early subs, set pieces, xG share, value."""
    def n(col):
        v = p.get(col)
        return "–" if v is None or v != v else f"{int(v)}"
    parts = [f"<b>{n('starts_season')}</b> starts of {n('team_fixtures_season')}",
             f"{n('sub_appearances_season')} off the bench",
             f"{n('early_subs_season')} subbed before 60'"]
    sp = set_piece_text(p)
    if sp:
        parts.append(sp)
    share = p.get("xg_share_season")
    if share is not None and share == share and p["position_code"] != "GKP":
        parts.append(f"<b>{share:.0%}</b> of team xG")
    ppm = p.get("points_per_million")
    if ppm is not None and ppm == ppm:
        parts.append(f"<b>{ppm:.1f}</b> pts per £m")
    return ('<div class="fs-meta" style="margin:2px 0 4px;font-size:12.5px">'
            + " · ".join(parts) + "</div>")


def scout_card(p, pool_size, fixtures):
    """p: one mart_player_current row (dict). fixtures: list of dicts for the next 5 GWs."""
    pos = p["position_code"]
    status = "" if p["status"] == "a" else (
        f'<span class="fs-status">{escape(p["news"] or p["status"])}</span>')
    diff = (p.get("points_per_match_l5_stable") or 0) - (p.get("points_per_match_season_stable") or 0)
    rank = p.get("points_per_match_season_rank")
    rank_txt = f"#{int(rank)} of {pool_size} {pos}s" if rank == rank and rank else "not ranked"

    tiles = [
        (fmt(p.get("total_points_season"), ".0f"), "Season points", f"GW1–{int(p['last_gameweek'] or 0)}"),
        (fmt(p.get("points_per_match_season"), ".2f"), "Points per match", rank_txt),
        (fmt(p.get("xpts_next_gw"), ".1f"), "Expected pts, next GW",
         f"next 5 GWs: {fmt(p.get('xpts_next5'), '.1f')}"),
        (f"{diff:+.2f}", "Form vs season", "last 5, stabilised"),
        (fmt(p.get("played_share_l5"), ".0%"), "Played, last 5", f"{fmt(p.get('avg_minutes_l5'), '.0f')} min avg"),
    ]
    html = [CSS, '<div class="fs-card">',
            '<div class="fs-head">',
            team_badge(p["current_team"], p.get("team_primary_colour"),
                       p.get("team_secondary_colour")),
            f'<div><div class="fs-name">{escape(p["web_name"])}{status}</div>',
            f'<div class="fs-meta">{escape(p["current_team"])} · {pos} · £{p["price"]:.1f}m · '
            f'owned by {p["selected_by_pct"]:.1f}%</div></div></div>',
            '<div class="fs-tiles">']
    for v, l, s in tiles:
        html.append(f'<div class="fs-tile"><div class="v">{v}</div><div class="l">{l}</div>'
                    f'<div class="s">{escape(s)}</div></div>')
    html.append("</div>")
    html.append(role_line(p))
    if p.get("is_breakout"):
        lasts = p.get("breakout_p_lasts") or 0
        kept = p.get("breakout_share_kept") or 0
        html.append(
            '<div class="fs-meta" style="margin:4px 0 6px;font-size:12.5px;color:#0b0b0b">'
            f'<span class="fs-status" style="margin:0 6px 0 0;border-color:#eb6834;color:#0b0b0b">'
            f'Breakout · {escape(p.get("breakout_reason") or "")}</span>'
            f'last 4: {p.get("breakout_recent_ppf") or 0:.1f} pts/fixture vs {p.get("breakout_baseline_ppf") or 0:.1f} '
            f'before · past "{escape(p.get("breakout_reason") or "")}" breakouts: {lasts:.0%} stayed above '
            f'baseline over the next 5, keeping {kept:.0%} of the jump</div>')

    if not p.get("is_rank_eligible"):
        html.append('<div class="fs-sec">Percentiles</div><div class="fs-na">Not enough '
                    'minutes yet for position percentiles (needs 40% of team minutes).</div>')
    else:
        current_sec = None
        for sec, label, pcol, rcol, rfmt, positions in SECTIONS:
            if positions and pos not in positions:
                continue
            if pos == "GKP" and sec == "Attack":
                continue   # keepers all tie near zero; saves and xGC carry the signal
            if sec != current_sec:
                html.append(f'<div class="fs-sec">{sec}</div>')
                current_sec = sec
            pct = p.get(pcol)
            width = 0 if pct is None or pct != pct else pct
            html.append(
                f'<div class="fs-row" title="{escape(label)}: {fmt(p.get(rcol), rfmt)} · '
                f'{ordinal(width)} percentile among {pos}s">'
                f'<div class="n">{escape(label)} <span class="raw">{fmt(p.get(rcol), rfmt)}</span></div>'
                f'<div class="fs-track"><div class="fs-fill" style="width:{width:.0f}%"></div></div>'
                f'<div class="p">{fmt(pct, ".0f")}</div></div>')

    # Next 5, coloured by what matters for this position (D38): clean-sheet chance for
    # defenders and keepers, expected goals for attackers.
    defender = pos in ("GKP", "DEF")
    html.append(f'<div class="fs-sec">Next fixtures · shaded by '
                f'{"clean-sheet chance" if defender else "expected team goals"}</div>'
                '<div class="fs-fx">')
    for f in fixtures:
        bg, ink = (ramp(f["clean_sheet_prob"], 0.1, 0.5) if defender
                   else ramp(f["expected_goals_for"], 0.8, 2.2))
        html.append(
            f'<div class="fs-chip" style="background:{bg};color:{ink}">'
            f'<div class="gw">GW{int(f["gameweek"])}</div>'
            f'<div class="opp">{escape(f["opponent"])} ({"H" if f["is_home"] else "A"})</div>'
            f'<div>{f["expected_goals_for"]:.1f} xG for · {f["clean_sheet_prob"]:.0%} CS</div></div>')
    if not fixtures:
        html.append('<div class="fs-na">No upcoming fixtures.</div>')
    html.append("</div>")
    html.append('<div class="fs-foot">Percentiles: 0–100 within position among players with '
                '40%+ of team minutes; higher is always better. Hover a bar for its value.</div>')
    html.append("</div>")
    return "".join(html)
