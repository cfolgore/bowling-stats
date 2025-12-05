import streamlit as st
import pandas as pd
from pathlib import Path

EXCEL_PATH = Path(__file__).parent / "Stevens Bowling Stats Seasonal.xlsx"
SHEET_SPARES = "Sheet2"
SHEET_OVERALL = "Sheet3"


@st.cache_data
def load_data():
    """Load overall + spare data from the Excel file."""
    xls = pd.ExcelFile(EXCEL_PATH)

    overall_df = pd.read_excel(xls, SHEET_OVERALL)
    spares_df = pd.read_excel(xls, SHEET_SPARES)

    # Just to be safe: ensure name is string
    overall_df["name"] = overall_df["name"].astype(str)
    spares_df["name"] = spares_df["name"].astype(str)

    return overall_df, spares_df


def build_spare_stats(spare_row):
    """
    For a single spare row (Sheet2-style; can be player or team-aggregated),
    build a list of dicts with:
      - pattern (e.g. '3,9,10' or '7')
      - made      (total conversions)
      - attempts  (total times that spare was left)
      - pct       (made / attempts, 0–1)

    IMPORTANT: We interpret the columns as:
      pattern        = TOTAL leaves (attempts)
      pattern Missed = MISSED leaves
    So: made = total - missed
    """
    stats = []

    for col in spare_row.index:
        # Skip the player name column if present
        if col == "name":
            continue

        # Skip the " Missed" columns on this pass; we pair them with the base col
        if isinstance(col, str) and col.endswith(" Missed"):
            continue

        total_col = col
        missed_col = f"{col} Missed"

        total = spare_row.get(total_col, 0)
        missed = spare_row.get(missed_col, 0)

        # Handle NaNs / weird types
        total = 0 if pd.isna(total) else int(total)
        missed = 0 if pd.isna(missed) else int(missed)

        # If no leaves, skip
        if total <= 0:
            continue

        attempts = total
        made = max(total - missed, 0)  # just in case bad data makes missed > total
        pct = made / attempts if attempts else 0.0

        stats.append(
            {
                "pattern": str(total_col),
                "made": made,
                "attempts": attempts,
                "pct": pct,
            }
        )

    # sort by make% ascending so worst spares are at the top
    stats.sort(key=lambda x: x["pct"])
    return stats


def parse_pattern_to_set(pattern: str):
    """'3,9,10' -> frozenset({3,9,10})  |  '7' -> frozenset({7})"""
    if not pattern:
        return frozenset()
    pins = []
    for part in str(pattern).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            pins.append(int(part))
        except ValueError:
            # if there's any weird label, ignore it
            continue
    return frozenset(pins)


# x-positions (columns) for pins, roughly symmetric
PIN_X = {
    1: 0.0,
    2: -0.5,
    3: 0.5,
    4: -1.0,
    5: 0.0,
    6: 1.0,
    7: -1.5,
    8: -0.5,
    9: 0.5,
    10: 1.5,
}


def is_split(pins_set: frozenset) -> bool:
    """
    Your split rule:
      - must NOT have the 1 pin
      - at least 2 pins standing
      - there must be at least one "gap" between standing pins
        at least as big as the gap between 2 and 3 (i.e. x difference >= 1.0)
        with no standing pin in between.
    """
    if 1 in pins_set:
        return False
    if len(pins_set) < 2:
        return False

    xs = sorted(PIN_X[p] for p in pins_set if p in PIN_X)
    if len(xs) < 2:
        return False

    # look for two pins with a full gap and no other pin between
    for i in range(len(xs)):
        for j in range(i + 1, len(xs)):
            if xs[j] - xs[i] >= 1.0:  # at least a 2–3 gap
                # check that no x is strictly between them
                between = any(xs[i] < x < xs[j] for x in xs)
                if not between:
                    return True

    return False


def classify_spare_type(pins_set: frozenset) -> str:
    """
    Classify spare as:
      - 'single' (one pin)
      - 'split' (per is_split)
      - 'multi' (multi-pin, non-split)
    """
    if len(pins_set) == 1:
        return "single"
    if is_split(pins_set):
        return "split"
    return "multi"


def ensure_pin_state():
    """Make sure we have a boolean selected-state for pins 1–10 in session_state."""
    for pin in range(1, 11):
        key = f"pin_{pin}"
        if key not in st.session_state:
            st.session_state[key] = False


def render_pin_deck(button_prefix: str):
    """
    Render the triangular pin deck (1–10) as circular buttons.
    button_prefix makes button keys unique per tab (e.g. 'player', 'team').
    Tapping a button toggles st.session_state['pin_X'].
    """
    ensure_pin_state()

    def pin_button(pin: int, col):
        sel_key = f"pin_{pin}"
        selected = st.session_state[sel_key]

        label = str(pin)

        if col.button(label, key=f"{button_prefix}_pin_btn_{pin}"):
            st.session_state[sel_key] = not selected

    # 7 columns per row to make a nice triangle
    # Row 4: positions 0,2,4,6 -> pins 7,8,9,10
    row4 = st.columns(7)
    for idx, pin in zip([0, 2, 4, 6], [7, 8, 9, 10]):
        pin_button(pin, row4[idx])

    # Row 3: positions 1,3,5 -> pins 4,5,6
    row3 = st.columns(7)
    for idx, pin in zip([1, 3, 5], [4, 5, 6]):
        pin_button(pin, row3[idx])

    # Row 2: positions 2,4 -> pins 2,3
    row2 = st.columns(7)
    for idx, pin in zip([2, 4], [2, 3]):
        pin_button(pin, row2[idx])

    # Row 1: position 3 -> pin 1
    row1 = st.columns(7)
    pin_button(1, row1[3])


def get_selected_pins():
    """Return (sorted list of selected pins, frozenset version)."""
    ensure_pin_state()
    selected_pins = sorted(
        [p for p in range(1, 11) if st.session_state[f"pin_{p}"]]
    )
    return selected_pins, frozenset(selected_pins)

def top_shooters_for_pattern(
    spares_df: pd.DataFrame,
    overall_df: pd.DataFrame,
    pattern: str,
    team_pct: float,
) -> pd.DataFrame:
    """
    For a given spare pattern name (e.g. '10' or '3,6,10'),
    compute per-player performance and return a table like:

      Player | Games | Made | Attempts | Make % | diff_vs_team

    Only includes players with attempts > 0, sorted by Make % desc.
    """
    # Try to interpret pattern as an int as well (for single-pin cols stored as numbers)
    try:
        pattern_int = int(pattern)
    except ValueError:
        pattern_int = None

    total_col_str = pattern            # e.g. "10"
    missed_col_str = f"{pattern} Missed"  # e.g. "10 Missed"

    rows = []
    for _, row in spares_df.iterrows():
        name = row["name"]

        # --- total leaves ---
        if total_col_str in row.index:
            total = row[total_col_str]
        elif pattern_int is not None and pattern_int in row.index:
            total = row[pattern_int]
        else:
            total = 0

        # --- missed leaves ---
        if missed_col_str in row.index:
            missed = row[missed_col_str]
        else:
            missed = 0

        total = 0 if pd.isna(total) else int(total)
        missed = 0 if pd.isna(missed) else int(missed)

        if total <= 0:
            continue

        attempts = total
        made = max(total - missed, 0)
        pct = made / attempts if attempts else 0.0
        diff_vs_team = (pct - team_pct) * 100.0

        rows.append(
            {
                "Player": name,
                "Made": made,
                "Attempts": attempts,
                "Make %": pct * 100.0,
                "diff_vs_team": diff_vs_team,
            }
        )

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    # Attach games for context
    games_df = overall_df[["name", "games"]].rename(
        columns={"name": "Player", "games": "Games"}
    )
    df = df.merge(games_df, on="Player", how="left")

    # Sort: best shooters first, then more volume
    df = df.sort_values(
        ["Make %", "Attempts"], ascending=[False, False]
    ).reset_index(drop=True)

   
    return df




def pin_lookup_section(label: str, stats_df: pd.DataFrame):
    """
    Given a stats_df with columns:
      - 'pattern'
      - 'made'
      - 'attempts'
      - 'pct'
      - 'make %'
      - 'pins_set'
      - (optional) 'diff_vs_team'
    use the current selected pins to show exact/contains matches.
    """
    selected_pins, selected_set = get_selected_pins()

    st.markdown("---")
    if not selected_pins:
        st.info(f"Select one or more pins above to look up a spare for **{label}**.")
        return

    st.write(f"{label} – selected leave: **{', '.join(map(str, selected_pins))}**")

    # Exact match: pattern whose pin set is exactly the selected set
    exact_df = stats_df[stats_df["pins_set"] == selected_set].copy()

    if exact_df.empty:
        st.warning("No exact spare with that **exact** combination recorded yet.")
    else:
        st.markdown("**Exact spare match**")
        cols = ["pattern", "made", "attempts", "make %"]
        if "diff_vs_team" in exact_df.columns:
            cols.append("diff_vs_team")
        show_exact = exact_df.loc[:, cols]
        st.dataframe(
            show_exact.style.format(
                {
                    "make %": "{:.1f}",
                    "diff_vs_team": "{:+.1f}%",
                }
            ),
            use_container_width=True,
        )

    # Any pattern that includes all selected pins (superset search)
    contains_mask = stats_df["pins_set"].apply(
        lambda s: selected_set.issubset(s)
    )
    contains_df = stats_df[contains_mask].copy()

    st.markdown("**All leaves containing these pins**")
    if contains_df.empty:
        st.write("No spare leaves contain all of those pins.")
    else:
        cols = ["pattern", "made", "attempts", "make %"]
        if "diff_vs_team" in contains_df.columns:
            cols.append("diff_vs_team")
        show_contains = contains_df.loc[:, cols]
        st.dataframe(
            show_contains.sort_values("attempts", ascending=False).style.format(
                {
                    "make %": "{:.1f}",
                    "diff_vs_team": "{:+.1f}%",
                }
            ),
            use_container_width=True,
        )


def main():
    st.set_page_config(
        page_title="Stevens Bowling – Player Spare Stats",
        page_icon="🎳",
        layout="wide",
    )

    # Make circular buttons for pins
    st.markdown(
        """
        <style>
        /* Circle buttons (for pins) */
        div[data-testid="stButton"] > button {
            border-radius: 50% !important;
            width: 3rem !important;
            height: 3rem !important;
            padding: 0 !important;
            text-align: center !important;
            font-weight: 600 !important;
            font-size: 1.1rem !important;
        }
        /* Tighten spacing around buttons */
        div[data-testid="stButton"] {
            margin: 0.15rem 0.25rem !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.title("🎳 Stevens Bowling – Spare & Team Dashboard")
    st.write(
        "Pick a bowler to see their stats, use the pin deck to look up specific leaves, "
        "or view combined team stats. Data comes from "
        "**Stevens Bowling Stats Seasonal.xlsx**."
    )

    # Load data
    try:
        overall_df, spares_df = load_data()
    except FileNotFoundError:
        st.error(
            f"Could not find Excel file at: `{EXCEL_PATH}`.\n\n"
            "Make sure `Stevens Bowling Stats Seasonal.xlsx` is in the same folder as `app.py`."
        )
        return

    players = overall_df["name"].sort_values().tolist()
    if not players:
        st.warning("No players found in the data.")
        return

    # Sidebar controls
    st.sidebar.header("Controls")
    selected_player = st.sidebar.selectbox("Choose a player", players)

    min_attempts = st.sidebar.slider(
        "Minimum attempts for 'best/worst' lists",
        min_value=1,
        max_value=10,
        value=3,
        step=1,
    )

    # Grab the selected player's rows
    overall_row = overall_df[overall_df["name"] == selected_player]
    spare_row = spares_df[spares_df["name"] == selected_player]

    if overall_row.empty or spare_row.empty:
        st.error(
            f"Could not find complete stats for player: {selected_player}. "
            "Check that they exist in both Sheet2 and Sheet3."
        )
        return

    overall_row = overall_row.iloc[0]
    spare_row = spare_row.iloc[0]

    # ---- Player overall stats ----
    games = overall_row["games"]
    pinfall = overall_row["pinfall"]
    frames = overall_row["frames"]
    strikes = overall_row["strikes"]
    doubles = overall_row["doubles"]
    spares = overall_row["spares"]
    spare_attempts = overall_row["spare attempts"]
    single_pin_spares = overall_row["single pin spares"]
    single_pin_attempts = overall_row["single pin attempts"]
    clean_games = overall_row["Clean Games"]
    high_game = overall_row["High Game"]

    avg = pinfall / games if games else 0
    strike_pct = strikes / frames if frames else 0
    spare_pct = spares / spare_attempts if spare_attempts else 0
    single_pin_pct = (
        single_pin_spares / single_pin_attempts if single_pin_attempts else 0
    )

    st.subheader(f"Player Overview – {selected_player}")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Average", f"{avg:.1f}")
        st.metric("Games", int(games))
        st.metric("Pinfall", int(pinfall))
    with col2:
        st.metric("Strike %", f"{strike_pct*100:.1f}%")
        st.metric("Strikes", int(strikes))
        st.metric("Doubles", int(doubles))
    with col3:
        st.metric("Spare %", f"{spare_pct*100:.1f}%")
        st.metric("Spares", f"{int(spares)}/{int(spare_attempts)}")
        st.metric("Single-pin %", f"{single_pin_pct*100:.1f}%")
    with col4:
        st.metric(
            "Single-pin spares", f"{int(single_pin_spares)}/{int(single_pin_attempts)}"
        )
        st.metric("Clean Games", int(clean_games))
        st.metric("High Game", int(high_game))

    st.markdown("---")

    # ---- Spare breakdown for selected player ----
    player_spare_stats = build_spare_stats(spare_row)
    if not player_spare_stats:
        st.write("No spare data available for this player.")
        return

    player_stats_df = pd.DataFrame(player_spare_stats)
    player_stats_df["make %"] = player_stats_df["pct"] * 100
    player_stats_df["pins_set"] = player_stats_df["pattern"].apply(parse_pattern_to_set)
    player_stats_df["type"] = player_stats_df["pins_set"].apply(classify_spare_type)

    # ---- Team spare breakdown (aggregated) ----
    # Sum all numeric spare columns across all players in Sheet2
    team_spare_row = spares_df.drop(columns=["name"]).sum(numeric_only=True)
    team_spare_stats = build_spare_stats(team_spare_row)
    team_stats_df = pd.DataFrame(team_spare_stats)
    team_stats_df["make %"] = team_stats_df["pct"] * 100
    team_stats_df["pins_set"] = team_stats_df["pattern"].apply(parse_pattern_to_set)
    team_stats_df["type"] = team_stats_df["pins_set"].apply(classify_spare_type)

    # ---- Diff vs team for each spare (player) ----
    team_pct_map = dict(zip(team_stats_df["pattern"], team_stats_df["pct"]))
    player_stats_df["team_pct"] = player_stats_df["pattern"].map(team_pct_map)
    player_stats_df["diff_vs_team"] = (
        (player_stats_df["pct"] - player_stats_df["team_pct"]) * 100
    )

    # Tabs: Player Dashboard + Pin lookup + Team
    tab_dash, tab_lookup, tab_team = st.tabs(
        ["📊 Player Dashboard", "🎯 Pin lookup", "👥 Team"]
    )

    # =============================
    # Tab 1 – Player Dashboard
    # =============================
    with tab_dash:
        st.subheader("Player spare tables")

        # Spare-type filters
        f_single = st.checkbox("Single pins", value=True, key="f_single")
        f_multi = st.checkbox("Multi-pin (non-splits)", value=True, key="f_multi")
        f_split = st.checkbox("Splits", value=True, key="f_split")

        # If nothing is selected, treat it as "all types"
        if not (f_single or f_multi or f_split):
            filtered_base = player_stats_df.copy()
        else:
            type_mask = False
            if f_single:
                type_mask |= player_stats_df["type"] == "single"
            if f_multi:
                type_mask |= player_stats_df["type"] == "multi"
            if f_split:
                type_mask |= player_stats_df["type"] == "split"

            filtered_base = player_stats_df[type_mask].copy()

        filtered = filtered_base[filtered_base["attempts"] >= min_attempts].copy()

        # Only keep rows where we actually have a team percentage
        filtered = filtered[filtered["diff_vs_team"].notna()].copy()

        # Worst = you are most BELOW team (big negative diff)
        worst_df = (
            filtered.sort_values("diff_vs_team", ascending=True)
            .head(10)
            .loc[:, ["pattern", "made", "attempts", "make %", "diff_vs_team"]]
        )

        # Best = you are most ABOVE team (big positive diff)
        best_df = (
            filtered.sort_values("diff_vs_team", ascending=False)
            .head(10)
            .loc[:, ["pattern", "made", "attempts", "make %", "diff_vs_team"]]
        )


        col_worst, col_best = st.columns(2)

        with col_worst:
            st.subheader(f"Worst spares (min {min_attempts} attempts)")
            if worst_df.empty:
                st.write("No spare leaves meet the filters + min attempts yet.")
            else:
                st.dataframe(
                    worst_df.style.format(
                        {
                            "make %": "{:.1f}",
                            "diff_vs_team": "{:+.1f}%",
                        }
                    ),
                    use_container_width=True,
                )

        with col_best:
            st.subheader(f"Best spares (min {min_attempts} attempts)")
            if best_df.empty:
                st.write("No spare leaves meet the filters + min attempts yet.")
            else:
                st.dataframe(
                    best_df.style.format(
                        {
                            "make %": "{:.1f}",
                            "diff_vs_team": "{:+.1f}%",
                        }
                    ),
                    use_container_width=True,
                )

        st.markdown("---")

        # Full breakdown
        with st.expander("Full spare breakdown (all leaves, with vs team)"):
            full_df = filtered_base.loc[
                :, ["pattern", "made", "attempts", "make %", "diff_vs_team", "type"]
            ].copy()
            st.dataframe(
                full_df.sort_values("pattern").style.format(
                    {
                        "make %": "{:.1f}",
                        "diff_vs_team": "{:+.1f}%",
                    }
                ),
                use_container_width=True,
            )

    # =============================
    # Tab 2 – Pin lookup (player-level)
    # =============================
    with tab_lookup:
        st.subheader("Spare lookup by pin combination – Player")
        st.write(
            "Click pins in the deck to define a leave. "
            "The app will show stats for that **exact** spare for "
            f"{selected_player}, plus all leaves that include those pins."
        )

        render_pin_deck(button_prefix="player")
        pin_lookup_section(label=selected_player, stats_df=player_stats_df)

    # =============================
    # Tab 3 – Team stats (including team pin lookup)
    # =============================
    with tab_team:
        st.subheader("Team overview (all players combined)")

        # Aggregate raw counts over all players
        team_games = overall_df["games"].sum()
        team_pinfall = overall_df["pinfall"].sum()
        team_frames = overall_df["frames"].sum()
        team_strikes = overall_df["strikes"].sum()
        team_doubles = overall_df["doubles"].sum()
        team_spares = overall_df["spares"].sum()
        team_spare_attempts = overall_df["spare attempts"].sum()
        team_single_pin_spares = overall_df["single pin spares"].sum()
        team_single_pin_attempts = overall_df["single pin attempts"].sum()
        team_clean_games = overall_df["Clean Games"].sum()
        # Max high game across the team
        team_high_game = overall_df["High Game"].max()

        team_avg = team_pinfall / team_games if team_games else 0
        team_strike_pct = team_strikes / team_frames if team_frames else 0
        team_spare_pct = (
            team_spares / team_spare_attempts if team_spare_attempts else 0
        )
        team_single_pin_pct = (
            team_single_pin_spares / team_single_pin_attempts
            if team_single_pin_attempts
            else 0
        )

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("Team average", f"{team_avg:.1f}")
            st.metric("Team games", int(team_games))
            st.metric("Team pinfall", int(team_pinfall))
        with c2:
            st.metric("Team strike %", f"{team_strike_pct*100:.1f}%")
            st.metric("Total strikes", int(team_strikes))
            st.metric("Team doubles", int(team_doubles))
        with c3:
            st.metric("Team spare %", f"{team_spare_pct*100:.1f}%")
            st.metric(
                "Total spares",
                f"{int(team_spares)}/{int(team_spare_attempts)}",
            )
            st.metric(
                "Team single-pin %",
                f"{team_single_pin_pct*100:.1f}%",
            )
        with c4:
            st.metric(
                "Single-pin spares",
                f"{int(team_single_pin_spares)}/{int(team_single_pin_attempts)}",
            )
            st.metric("Total clean games", int(team_clean_games))
            st.metric("High game (team best)", int(team_high_game))

        st.markdown("---")
        st.subheader("Per-player summary")

        # Build a ranking table per player
        players_df = overall_df.copy()
        players_df["average"] = players_df["pinfall"] / players_df["games"]
        players_df["strike %"] = (
            players_df["strikes"]
            / players_df["frames"].where(players_df["frames"] != 0, 1)
        ) * 100
        players_df["spare %"] = (
            players_df["spares"]
            / players_df["spare attempts"].where(
                players_df["spare attempts"] != 0, 1
            )
        ) * 100
        players_df["single-pin %"] = (
            players_df["single pin spares"]
            / players_df["single pin attempts"].where(
                players_df["single pin attempts"] != 0, 1
            )
        ) * 100

        # Column order & display
        show_cols = [
            "name",
            "games",
            "average",
            "strike %",
            "spare %",
            "single-pin %",
            "Clean Games",
            "High Game",
        ]

        table_df = players_df.loc[:, show_cols].rename(
            columns={
                "name": "Player",
                "games": "Games",
                "average": "Average",
                "strike %": "Strike %",
                "spare %": "Spare %",
                "single-pin %": "Single-pin %",
                "Clean Games": "Clean games",
                "High Game": "High game",
            }
        )

        # Sort by average desc by default
        table_df = table_df.sort_values("Average", ascending=False)

        st.dataframe(
            table_df.style.format(
                {
                    "Average": "{:.1f}",
                    "Strike %": "{:.1f}",
                    "Spare %": "{:.1f}",
                    "Single-pin %": "{:.1f}",
                }
            ),
            use_container_width=True,
        )

        st.markdown("---")
        st.subheader("Team spare lookup by pin combination")

        st.write(
            "Use the pin deck below to pick a leave, then see the top spare shooters "
            "on that exact spare across the team."
        )

        # Reuse the same deck, just different button keys for this tab
        render_pin_deck(button_prefix="team")

        # Figure out which spare we are talking about
        selected_pins, selected_set = get_selected_pins()

        if not selected_pins:
            st.info("Select one or more pins above to choose a spare.")
        else:
            st.write(f"Selected leave: **{', '.join(map(str, selected_pins))}**")

            # Find the exact pattern in team_stats_df
            match = team_stats_df[team_stats_df["pins_set"] == selected_set].copy()

            if match.empty:
                st.warning(
                    "The team has no recorded attempts on that exact spare yet."
                )
            else:
                pattern = match.iloc[0]["pattern"]
                team_pct = match.iloc[0]["pct"]
                team_made = match.iloc[0]["made"]
                team_attempts = match.iloc[0]["attempts"]

                st.markdown(
                    f"**Team on {pattern}:** {team_made}/{team_attempts} "
                    f"({team_pct*100:.1f}%)"
                )

                shooters_df = top_shooters_for_pattern(
                    spares_df=spares_df,
                    overall_df=overall_df,
                    pattern=pattern,
                    team_pct=team_pct,
                )

                if shooters_df.empty:
                    st.write(
                        "No individual player has any recorded attempts on this spare."
                    )
                else:
                    st.markdown("**Individuals on this leave**")
                    st.dataframe(
                        shooters_df.style.format(
                            {
                                "Make %": "{:.1f}%",
                                "diff_vs_team": "{:+.1f}%",
                            }
                        ),
                        use_container_width=True,
                    )
        st.markdown("---")
        st.subheader("Team spare ranking by type")

        spare_type_choice = st.radio(
            "Choose spare type to rank:",
            ["Single pins", "Multi-pin (non-splits)", "Splits"],
            index=0,
            key="team_spare_type_ranking",
            horizontal=True,
        )

        if spare_type_choice == "Single pins":
            type_key = "single"
        elif spare_type_choice == "Multi-pin (non-splits)":
            type_key = "multi"
        else:
            type_key = "split"

        type_df = team_stats_df[team_stats_df["type"] == type_key].copy()

        if type_df.empty:
            st.info("The team has no recorded attempts for that spare type yet.")
        else:
            # Rank: most common first
            type_df = type_df.sort_values("attempts", ascending=False)

            show_cols = ["pattern", "made", "attempts", "make %"]
            st.dataframe(
                type_df.loc[:, show_cols].style.format(
                    {
                        "make %": "{:.1f}",
                    }
                ),
                use_container_width=True,
            )




if __name__ == "__main__":
    main()
