"""fpl-scout app (D40, D53, D54, D59-D70). Local only.

Run from the repo root:  .venv/Scripts/streamlit run app/app.py
Reads data/dev.duckdb read-only, so run `dbt build` first, and not at the same time.
"""
import pathlib
import sys

import duckdb
import streamlit as st

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from charts import bar_chart, bullets, line_chart, points_strip, waterfall  # noqa: E402
from components import scout_card  # noqa: E402
from views import (PAGE_CSS, compare_card, fixture_grid, ranked_list,  # noqa: E402
                   section_title)

DB = pathlib.Path(__file__).resolve().parent.parent / "data" / "dev.duckdb"

# Reference palette from the dataviz method, validated: the first 5 categorical slots pass
# every hard gate (adjacent CVD dE 9.1, normal 19.6). Three are below 3:1 contrast, so every
# line carries an end label and every chart a table view.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
WINDOW_COLOURS = {"l5": SERIES[0], "l10": SERIES[1], "season": "#898781"}
STABLE_HELP = ("Stabilised for small samples (D57, D58, D64): (value x exposure + K x prior) / "
               "(exposure + K), K = 3 matches or 270 minutes. Season leans on the position "
               "average early on; last 5 and last 10 lean on his own season level. Hover a "
               "gameweek for raw values.")
WINDOWS = [("l5", "Last 5"), ("l10", "Last 10"), ("season", "Season")]


def card(title, help_text=None):
    """A bordered container styled as a card, with the card's section title."""
    c = st.container(border=True)
    c.markdown(section_title(title), unsafe_allow_html=True, help=help_text)
    return c


@st.cache_data(ttl=600)
def q(sql, params=None):
    with duckdb.connect(str(DB), read_only=True) as con:
        return con.execute(sql, params or []).df()


def show_svg(html):
    """SVG charts go through st.markdown: st.html strips <svg> (DOMPurify), so charts there
    render empty. Caught in a real-browser check, not by the headless AppTest."""
    st.markdown(html, unsafe_allow_html=True)


def clean(values):
    return [None if v != v else float(v) for v in values]


def window_lines(r, metric, fmt=".2f", ref=None, height=210, w=1100):
    """Last 5 / last 10 / season stabilised lines; season as a soft grey area."""
    series = [dict(name=name, values=clean(r[f"{metric}_{w}_stable"]),
                   raw=clean(r[f"{metric}_{w}"]), colour=WINDOW_COLOURS[w],
                   area=(w == "season")) for w, name in WINDOWS]
    return line_chart(r.gameweek.tolist(), series, height=height, y_fmt=fmt, ref=ref, w=w)


st.set_page_config(page_title="fpl-scout", layout="wide")
st.markdown(PAGE_CSS, unsafe_allow_html=True)
st.title("fpl-scout")

if not DB.exists():
    st.error(f"{DB} not found. Run the fetch, load and dbt build first.")
    st.stop()

current = q("select * from main.mart_player_current")
st.caption(f"Data as of {current.as_of_date.max():%d %b %Y}, through "
           f"GW{int(current.last_gameweek.max())}. Points per match count matches played; "
           "predictors are per 90.")

current["label"] = (current.web_name + " (" + current.current_team + ", "
                    + current.position_code + ")")
# Every player list is ordered by stabilised last-5 points per match, best first, so a single
# big cameo can't put a fringe player at the top.
labels = current.sort_values("points_per_match_l5_stable", ascending=False,
                             na_position="last").label.tolist()
label_to_id = dict(zip(current.label, current.player_id))


def position_labels(pos):
    """Player labels for a position filter value ('All' or None means every position)."""
    if not pos or pos == "All":
        return labels
    keep = set(current[current.position_code == pos].label)
    return [l for l in labels if l in keep]


def pool_size(pos):
    return int(((current.position_code == pos) & current.is_rank_eligible).sum())


def position_avg(pos, col):
    pool = current[(current.position_code == pos) & current.is_rank_eligible]
    return float(pool[col].mean()) if len(pool) else float("nan")


def rolling_for(ids):
    return q("""
        select r.*, c.web_name
        from main.fct_player_rolling r join main.mart_player_current c using (player_id)
        where r.player_id in (select unnest(?)) order by kickoff_at""", [list(ids)])


tab_scout, tab_rank, tab_compare, tab_fixtures = st.tabs(
    ["Scout report", "Rankings", "Compare", "Fixture difficulty"])

# ---------------------------------------------------------------- Scout report
with tab_scout:
    # Position filter, then a removable single pick (a chip with x, like Compare).
    scout_pos = st.segmented_control("Position", ["All", "GKP", "DEF", "MID", "FWD"],
                                     default="All", key="scout_pos")
    scout_opts = position_labels(scout_pos)
    pick_key = f"scout_pick_{scout_pos}"

    def keep_latest():
        """One player at a time, but the search stays open: picking another replaces him."""
        v = st.session_state[pick_key]
        if len(v) > 1:
            st.session_state[pick_key] = [v[-1]]

    chosen = st.multiselect("Player", scout_opts, scout_opts[:1], key=pick_key,
                            on_change=keep_latest,
                            placeholder=f"Search {'all' if scout_pos in (None, 'All') else scout_pos} players")
    if not chosen:
        st.info("Pick a player to see his scout report.")
    else:
        who = chosen[0]
        pid = label_to_id[who]
        me = current[current.player_id == pid].iloc[0].to_dict()
        pos = me["position_code"]
        nxt = q("""select gameweek, is_home, expected_goals_for, clean_sheet_prob, opponent
                   from main.fct_team_next_fixtures where team_id = ? order by kickoff_at""",
                [int(me["current_team_id"])])
        st.html(scout_card(me, pool_size(pos), nxt.to_dict("records")))

        r = rolling_for([pid])
        m = q("""
            select f.*, p.defensive_contribution_points, o.team_short_name as opponent
            from main.fct_player_fixtures f
            join main.int_player_fixture_points p using (player_id, fixture_id)
            join main.stg_fpl__teams o on o.team_id = f.opponent_team_id
            where f.player_id = ? order by f.kickoff_at""", [int(pid)])
        played = m[m.is_played]

        # ---- Form (D55, D56)
        with card("Form · points per match", STABLE_HELP):
            view = st.radio("View", ["Points per match", "Form vs season"], horizontal=True,
                            label_visibility="collapsed")
            if view == "Points per match":
                show_svg(window_lines(r, "points_per_match"))
            else:
                diff = (r.points_per_match_l5_stable - r.points_per_match_season_stable).tolist()
                show_svg(bar_chart(r.gameweek.tolist(), clean(diff), height=200, fmt="+.1f",
                                  diverging=True,
                                  titles=[f"GW{g}: {d:+.2f} ppm vs his season" for g, d in
                                          zip(r.gameweek, diff)]))
                st.html('<div class="fs-note">Above zero: last 5 running hotter than his season '
                        'level. Below: colder.</div>')

        # ---- Where the points came from (D68)
        with card("Where the points came from"):
            st.html(points_strip(m.to_dict("records")))
            tot = q("""select sum(appearance_points) a, sum(goal_points) g, sum(assist_points) s,
                              sum(clean_sheet_points) cs, sum(bonus_points) b,
                              sum(defensive_contribution_points) dc,
                              sum(save_points + penalty_save_points) sv,
                              sum(goals_conceded_points + yellow_card_points + red_card_points
                                  + own_goal_points + penalty_miss_points) neg
                       from main.int_player_fixture_points where player_id = ?""",
                    [int(pid)]).iloc[0]
            show_svg(waterfall([("Appearance", tot.a), ("Goals", tot.g), ("Assists", tot.s),
                               ("Clean sheets", tot.cs), ("Bonus", tot.b),
                               ("Def. contribution", tot.dc), ("Saves", tot.sv),
                               ("Deductions", tot.neg)]))

        # ---- Attack (D69)
        c1, c2 = st.columns(2)
        for col, (actual, expected, label, per90) in zip(
                (c1, c2), [("goals_scored", "xg", "Goals vs xG", "xg_per90"),
                           ("assists", "xa", "Assists vs xA", "xa_per90")]):
            with col:
                with card(label):
                    cum_a = played[actual].cumsum().tolist()
                    cum_x = played[expected].cumsum().tolist()
                    gws = played.gameweek.tolist()
                    name_a = "Goals" if actual == "goals_scored" else "Assists"
                    show_svg(line_chart(gws, [
                        dict(name=name_a, values=clean(cum_a), colour=SERIES[0]),
                        dict(name="xG" if expected == "xg" else "xA", values=clean(cum_x),
                             colour="#898781", dashed=True, area=True)], height=200, y_fmt=".1f", w=520))
                    if gws:
                        gap = cum_a[-1] - cum_x[-1]
                        verdict = ("running hot: likely to cool" if gap > 1.5 else
                                   "running cold: due a rise" if gap < -1.5 else
                                   "in line with his chances")
                        st.html(f'<div class="fs-note"><b>{gap:+.1f}</b> {name_a.lower()} vs '
                                f'expected, {verdict}.</div>')
                    st.html(bullets(
                        [(n, me.get(f"{per90}_{w}_stable")) for w, n in WINDOWS],
                        position_avg(pos, f"{per90}_season"), f"{pos} average", fmt=".2f"))
                    show_svg(window_lines(r, per90, height=170, w=520))

        # ---- Bonus and defensive work (D70)
        c3, c4 = st.columns(2)
        with c3:
            with card("BPS per match · bonus won on top"):
                show_svg(bar_chart(
                    played.gameweek.tolist(), clean(played.bps), height=180,
                    colours=[SERIES[0] if b else "#c3c2b7" for b in played.bonus],
                    marks=[f"+{int(b)}" if b else "" for b in played.bonus],
                    titles=[f"GW{g} v {o}: {b:.0f} BPS, {int(bn)} bonus" for g, o, b, bn in
                            zip(played.gameweek, played.opponent, played.bps, played.bonus)], w=520))
                show_svg(window_lines(r, "bps_per90", fmt=".1f", height=160, w=520))
        with c4:
            if pos == "GKP":
                with card("Saves per match"):
                    show_svg(bar_chart(played.gameweek.tolist(), clean(played.saves), height=180,
                                      threshold=(3, "1 pt per 3"), w=520))
                    show_svg(window_lines(r, "saves_per90", fmt=".1f", height=160, w=520))
            else:
                thr = q("""select threshold from main.scoring_thresholds
                           where rule = 'defensive_contribution_min' and position_code = ?""",
                        [pos])
                t = int(thr.threshold.iloc[0]) if len(thr) else None
                with card("Defensive contribution per match"):
                    hits = int((played.defensive_contribution >= t).sum()) if t else 0
                    near = int(played.defensive_contribution.between(t - 2, t - 1).sum()) if t else 0
                    show_svg(bar_chart(
                        played.gameweek.tolist(), clean(played.defensive_contribution), height=180,
                        colours=[SERIES[0] if t and v >= t else "#c3c2b7"
                                 for v in played.defensive_contribution],
                        threshold=(t, f"{t} = +2 pts") if t else None,
                        titles=[f"GW{g} v {o}: {v:.0f} actions" for g, o, v in
                                zip(played.gameweek, played.opponent,
                                    played.defensive_contribution)], w=520))
                    if t:
                        st.html(f'<div class="fs-note">Hit the threshold in <b>{hits} of '
                                f'{len(played)}</b> matches; near misses ({t - 2}–{t - 1}): '
                                f'<b>{near}</b>.</div>')
                    show_svg(window_lines(r, "defensive_contribution_per90", fmt=".1f",
                                         height=160, w=520))

        with st.expander("Table view"):
            st.dataframe(m, hide_index=True, width="stretch")


# ---------------------------------------------------------------- Rankings
with tab_rank:
    f1, f2, f3, f4, f5 = st.columns([2, 3, 2, 2, 2])
    positions = f1.multiselect("Position", ["GKP", "DEF", "MID", "FWD"], ["MID"])
    teams = f2.multiselect("Team", sorted(current.current_team.unique()))
    lo, hi = float(current.price.min()), float(current.price.max())
    price = f3.slider("Price (£m)", lo, hi, (lo, hi), 0.5)
    sort_options = {
        "Points per match": "points_per_match_season", "Total points": "total_points_season",
        "Points per £m": "points_per_million",
        "Form (last 5, stabilised)": "points_per_match_l5_stable",
        "xG per 90": "xg_per90_season", "xA per 90": "xa_per90_season",
        "BPS per 90": "bps_per90_season", "Owned %": "selected_by_pct",
    }
    sort_by = f4.selectbox("Sort by", list(sort_options))
    eligible_only = f5.toggle("Rank-eligible only", True,
                              help="At least 40% of team minutes this season (D13).")

    df = current[current.position_code.isin(positions or ["GKP", "DEF", "MID", "FWD"])
                 & current.price.between(*price)]
    if teams:
        df = df[df.current_team.isin(teams)]
    if eligible_only:
        df = df[df.is_rank_eligible]
    spark = q("""select player_id, list(total_points order by kickoff_at) as recent
                 from (select *, row_number() over (partition by player_id
                                                   order by kickoff_at desc) n
                       from main.fct_player_rolling where is_played)
                 where n <= 8 group by player_id""")
    df = df.merge(spark, on="player_id", how="left").sort_values(
        sort_options[sort_by], ascending=False, na_position="last")

    # Mini bars follow the position: defenders show defence, attackers show attack.
    only = set(positions or [])
    if only == {"GKP"}:
        bars = [("Saves/90", "saves_per90_season_pctl", ".1f"),
                ("xGC/90", "xgc_per90_season_pctl", ".2f"),
                ("CS next 5", "next5_clean_sheet_prob_pctl", ".0%")]
    elif only and only <= {"GKP", "DEF"}:
        bars = [("xGC/90", "xgc_per90_season_pctl", ".2f"),
                ("CS next 5", "next5_clean_sheet_prob_pctl", ".0%"),
                ("DefCon/90", "defensive_contribution_per90_season_pctl", ".1f")]
    else:
        bars = [("xG/90", "xg_per90_season_pctl", ".2f"), ("xA/90", "xa_per90_season_pctl", ".2f"),
                ("BPS/90", "bps_per90_season_pctl", ".1f")]
    top = df.head(50).to_dict("records")
    show_svg(ranked_list(top, bars))
    st.caption(f"Top {len(top)} of {len(df)} players. Percentiles are within position among "
               "eligible players; hover a row for news, a bar in Last 8 for its points.")
    with st.expander("Table view"):
        st.dataframe(df.drop(columns=["recent", "label"]), hide_index=True, width="stretch")

# ---------------------------------------------------------------- Compare up to 5
with tab_compare:
    cmp_pos = st.segmented_control("Position", ["All", "GKP", "DEF", "MID", "FWD"],
                                   default="All", key="cmp_pos")
    # Two parts (Streamlit can't hide a chosen player from a picker's options):
    # an "Add" search that lists only the filtered position, and the chips you are comparing,
    # which keep players of any position and each have an x to remove.
    ss = st.session_state
    if "cmp_list" not in ss:
        ss.cmp_list = labels[:3]

    def add_player():
        v = ss.cmp_add
        if v and v not in ss.cmp_list and len(ss.cmp_list) < 5:
            ss.cmp_list = ss.cmp_list + [v]
        ss.cmp_add = None

    def sync_chips():
        ss.cmp_list = list(ss.cmp_chips)

    a1, a2 = st.columns([2, 3])
    pos_word = "all" if cmp_pos in (None, "All") else cmp_pos
    a1.selectbox("Add a player",
                 [l for l in position_labels(cmp_pos) if l not in ss.cmp_list],
                 index=None, key="cmp_add", on_change=add_player,
                 placeholder=f"Search {pos_word} players",
                 disabled=len(ss.cmp_list) >= 5,
                 help="Up to 5. Sorted by stabilised last-5 points per match.")
    ss.cmp_chips = ss.cmp_list
    picks = a2.multiselect(f"Comparing ({len(ss.cmp_list)} of 5)", ss.cmp_list, key="cmp_chips",
                           on_change=sync_chips, placeholder="Add players on the left")
    if picks:
        ids = [label_to_id[p] for p in picks]
        # Colour follows the player in pick order, so removing one never repaints the rest.
        cols = st.columns(len(picks))
        for i, (col, p) in enumerate(zip(cols, picks)):
            row = current[current.player_id == label_to_id[p]].iloc[0].to_dict()
            col.html(compare_card(row, SERIES[i], pool_size(row["position_code"])))

        metric_options = {
            "Points per match": ("points_per_match", ".2f"),
            "xG per 90": ("xg_per90", ".2f"), "xA per 90": ("xa_per90", ".2f"),
            "BPS per 90": ("bps_per90", ".1f"),
            "Defensive contribution per 90": ("defensive_contribution_per90", ".1f"),
            "Minutes per match": ("minutes_per_match", ".0f"),
            "Played share of team fixtures": ("played_share", ".0%"),
        }
        with card("Trend", STABLE_HELP):
            m1, m2 = st.columns([3, 2])
            metric = m1.selectbox("Metric", list(metric_options), label_visibility="collapsed")
            window = m2.radio("Window", ["Last 5", "Last 10", "Season"], horizontal=True,
                              label_visibility="collapsed")
            col, fmt = metric_options[metric]
            w = {"Last 5": "l5", "Last 10": "l10", "Season": "season"}[window]
            r = rolling_for(ids)
            gws = sorted(r.gameweek.unique().tolist())
            series = []
            for i, p in enumerate(picks):
                d = r[r.player_id == label_to_id[p]].groupby("gameweek").last()
                d = d.reindex(gws)
                series.append(dict(name=p.split(" (")[0], colour=SERIES[i],
                                   values=clean(d[f"{col}_{w}_stable"]),
                                   raw=clean(d[f"{col}_{w}"])))
            show_svg(line_chart(gws, series, height=260, y_fmt=fmt))

# ---------------------------------------------------------------- Fixture difficulty
with tab_fixtures:
    fx = q("select * from main.fct_team_next_fixtures")
    if fx.empty:
        st.info("No upcoming fixtures.")
    else:
        side = st.radio("For", ["Attackers: expected goals scored",
                                "Defenders & keepers: clean-sheet chance"], horizontal=True,
                        label_visibility="collapsed")
        st.caption("One opponent can be easy for attackers and hard for defenders (D38). "
                   "Ratings use xG, recency-weighted and opponent-adjusted.")
        tc = q("select team_short_name, primary_colour, secondary_colour from main.team_colours")
        colours = {r.team_short_name: (r.primary_colour, r.secondary_colour)
                   for r in tc.itertuples()}
        st.html(fixture_grid(fx, "attack" if side.startswith("Attackers") else "defence",
                             colours))
        with st.expander("Table view"):
            st.dataframe(fx[["gameweek", "team", "opponent", "is_home", "expected_goals_for",
                             "expected_goals_against", "clean_sheet_prob"]]
                         .sort_values(["gameweek", "team"]), hide_index=True, width="stretch")
