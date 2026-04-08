"""analyzer/stats.py — Compute statistics from parsed run data."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional, Set

from .parser import (
    CHOICE_SOURCES,
    extract_ancients,
    extract_card_choices,
    extract_deck_cards,
    extract_encounters,
    extract_events,
    extract_potion_events,
    extract_relic_events,
    extract_rest_sites,
    extract_shop_events,
    get_character,
    get_run_id,
    is_abandoned_first_floor,
    is_solo_run,
    iter_nodes,
    run_acts_reached,
    run_deck,
    run_deck_size,
    run_final_gold,
    run_final_hp,
    run_final_max_hp,
    run_killed_by,
    run_total_damage,
)


# ── Filtering & deduplication ─────────────────────────────────────────────────

def filter_runs(
    runs: List[Dict[str, Any]],
    *,
    character: Optional[str] = None,
    ascension: Optional[int] = None,
    ancient: Optional[str] = None,
    exclude_multiplayer: bool = True,
    exclude_abandoned: bool = True,
) -> List[Dict[str, Any]]:
    """Apply standard filters and seed-deduplication to a run list.

    Parameters
    ----------
    runs:
        All loaded run dicts.
    character:
        If provided, keep only runs matching this character (e.g. "IRONCLAD").
    ascension:
        If provided, keep only runs at exactly this ascension level.
    ancient:
        If provided, keep only runs in which this Ancient was encountered
        at least once (e.g. "NEOW", "PAEL").
    exclude_multiplayer:
        When True, discard runs with ``players.length > 1``.
    exclude_abandoned:
        When True, discard runs abandoned on or before the first floor.
        (Later-abandoned runs are retained — they still contain useful data.)
    """
    filtered: List[Dict[str, Any]] = []
    seen_seeds: Set[str] = set()

    for run in runs:
        if exclude_multiplayer and not is_solo_run(run):
            continue
        if exclude_abandoned and is_abandoned_first_floor(run):
            continue
        if character and get_character(run) != character:
            continue
        if ascension is not None and run.get("ascension", 0) != ascension:
            continue
        if ancient:
            names = {ev["name"] for ev in extract_ancients(run)}
            if ancient not in names:
                continue

        # Dedup by seed
        rid = get_run_id(run)
        if rid in seen_seeds:
            continue
        seen_seeds.add(rid)
        filtered.append(run)

    return filtered


def get_characters(runs: List[Dict[str, Any]]) -> List[str]:
    """Return sorted unique character names present in *runs*."""
    chars = {get_character(r) for r in runs}
    return sorted(chars)


def get_ascensions(runs: List[Dict[str, Any]]) -> List[int]:
    """Return sorted unique ascension levels present in *runs*."""
    levels = {r.get("ascension", 0) for r in runs}
    return sorted(levels)


def get_ancients(runs: List[Dict[str, Any]]) -> List[str]:
    """Return sorted unique ancient names present in *runs*."""
    names: Set[str] = set()
    for run in runs:
        for ev in extract_ancients(run):
            names.add(ev["name"])
    return sorted(names)


# ── Overview ──────────────────────────────────────────────────────────────────

def compute_overview(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(runs)
    if total == 0:
        return {
            "total_runs": 0,
            "wins": 0,
            "win_rate": 0.0,
            "avg_damage_taken": 0.0,
            "avg_deck_size": 0.0,
            "avg_run_time_s": 0.0,
            "acts_reached": {},
            "kills_by": {},
            "longest_win_streak": 0,
            "total_encounters": 0,
            "total_boss_kills": 0,
            "total_damage_taken": 0,
            "total_hp_healed": 0,
            "total_gold_earned": 0,
            "total_cards_picked": 0,
            "fastest_win_s": None,
            "close_call_wins": 0,
        }

    wins = sum(1 for r in runs if r.get("win", False))
    win_rate = wins / total

    damages = [run_total_damage(r) for r in runs]
    avg_damage = sum(damages) / total

    deck_sizes = [run_deck_size(r) for r in runs]
    avg_deck = sum(deck_sizes) / total

    run_times = [r.get("run_time", 0) for r in runs if r.get("run_time")]
    avg_time = sum(run_times) / len(run_times) if run_times else 0.0

    acts_dist: Dict[str, int] = defaultdict(int)
    for r in runs:
        acts_dist[str(run_acts_reached(r))] += 1

    kills_by: Dict[str, int] = defaultdict(int)
    for r in runs:
        if r.get("win"):
            continue
        killer = run_killed_by(r) or "Unknown"
        kills_by[str(killer)] += 1

    # ── Fun / lifetime stats ───────────────────────────────────────────────────

    # Longest consecutive win streak (ordered by start_time)
    sorted_runs = sorted(runs, key=lambda r: r.get("start_time") or "")
    longest_streak = cur_streak = 0
    for r in sorted_runs:
        if r.get("win"):
            cur_streak += 1
            longest_streak = max(longest_streak, cur_streak)
        else:
            cur_streak = 0

    # Encounter totals
    total_encounters = 0
    total_boss_kills = 0
    for r in runs:
        for enc in extract_encounters(r):
            total_encounters += 1
            if enc.get("type") == "boss":
                total_boss_kills += 1

    # Lifetime damage and healing
    total_damage_taken = sum(damages)
    total_hp_healed = sum(
        stats.get("hp_healed", 0)
        for r in runs
        for _, stats, _ in iter_nodes(r)
    )

    # Total gold earned
    total_gold_earned = sum(
        stats.get("gold_gained", 0)
        for r in runs
        for _, stats, _ in iter_nodes(r)
    )

    # Total cards picked (explicit picks only; relic-added cards tracked separately)
    total_cards_picked = sum(
        len(ev.get("picked", []))
        for r in runs
        for ev in extract_card_choices(r)
    )

    # Fastest win time
    win_times = [r.get("run_time") for r in runs if r.get("win") and r.get("run_time")]
    fastest_win_s: Optional[float] = min(win_times) if win_times else None

    # Close-call wins: won with ≤5 HP remaining
    close_call_wins = 0
    for r in runs:
        if r.get("win"):
            final_hp = run_final_hp(r)
            if final_hp is not None and final_hp <= 5:
                close_call_wins += 1

    return {
        "total_runs": total,
        "wins": wins,
        "win_rate": round(win_rate, 4),
        "avg_damage_taken": round(avg_damage, 1),
        "avg_deck_size": round(avg_deck, 1),
        "avg_run_time_s": round(avg_time, 1),
        "acts_reached": dict(sorted(acts_dist.items())),
        "kills_by": dict(
            sorted(kills_by.items(), key=lambda x: x[1], reverse=True)[:15]
        ),
        "longest_win_streak": longest_streak,
        "total_encounters": total_encounters,
        "total_boss_kills": total_boss_kills,
        "total_damage_taken": total_damage_taken,
        "total_hp_healed": total_hp_healed,
        "total_gold_earned": total_gold_earned,
        "total_cards_picked": total_cards_picked,
        "fastest_win_s": fastest_win_s,
        "close_call_wins": close_call_wins,
    }


# ── Cards ─────────────────────────────────────────────────────────────────────

def compute_cards(
    runs: List[Dict[str, Any]],
    min_picked: int = 1,
    known_cards: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Compute per-card pick-rate, win-rate, and upgrade statistics.

    Win rate and pick rate are tracked **per run** (not per offering) to avoid
    inflating stats when a card is offered multiple times in one run.

    Upgrade stats (avg_upgrade_level, pct_upgraded) are derived from the
    final deck state across all runs where the card appears.

    If *known_cards* is provided, every card in that list is included in the
    output even if it never appeared in any run (those entries have all-zero
    counts and null rates).
    """
    # run-level sets: offered_runs[card], picked_runs[card], added_runs[card], win_runs[card]
    offered_runs: Dict[str, Set[str]] = defaultdict(set)
    picked_runs: Dict[str, Set[str]] = defaultdict(set)
    added_runs: Dict[str, Set[str]] = defaultdict(set)
    win_runs: Dict[str, Set[str]] = defaultdict(set)

    # upgrade tracking from final decks: list of upgrade levels per card
    deck_upgrade_levels: Dict[str, List[int]] = defaultdict(list)

    for run in runs:
        run_id = get_run_id(run)
        run_win = run.get("win", False)
        for event in extract_card_choices(run):
            for card in event["offered"]:
                if card:
                    offered_runs[card].add(run_id)
            for picked in event.get("picked", []):
                if picked:
                    picked_runs[picked].add(run_id)
                    if run_win:
                        win_runs[picked].add(run_id)
            for added in event.get("added", []):
                if added:
                    added_runs[added].add(run_id)
                    if run_win:
                        win_runs[added].add(run_id)

        for deck_card in extract_deck_cards(run):
            card = deck_card["card"]
            deck_upgrade_levels[card].append(deck_card["upgrade_level"])

    # All cards that have any run data
    all_cards: Set[str] = set(offered_runs.keys()) | set(added_runs.keys())

    # Seed with known cards so unseen ones still appear as zero-value rows
    known_cards_set: Set[str] = set(known_cards) if known_cards else set()
    all_cards.update(known_cards_set)

    results: List[Dict[str, Any]] = []
    for card in all_cards:
        offered_set = offered_runs.get(card, set())
        picked_set = picked_runs.get(card, set())
        added_set = added_runs.get(card, set())
        # Always include cards from the known list (e.g. from card_data.json) even
        # when they have 0 picked_runs.  The min_picked threshold still applies
        # to cards that only appear in run data (not in the known list).
        if len(picked_set) < min_picked and card not in known_cards_set:
            continue
        won_set = win_runs.get(card, set())
        pick_rate = len(picked_set) / len(offered_set) if offered_set else 0.0
        # Win rate covers all runs where the card was acquired (picked or relic-added).
        acquired_set = picked_set | added_set
        win_rate = len(won_set) / len(acquired_set) if acquired_set else None

        upgrade_levels = deck_upgrade_levels.get(card, [])
        avg_upgrade = round(sum(upgrade_levels) / len(upgrade_levels), 4) if upgrade_levels else None
        pct_upgraded = round(sum(1 for u in upgrade_levels if u > 0) / len(upgrade_levels), 4) if upgrade_levels else None

        results.append(
            {
                "card": card,
                "offered_runs": len(offered_set),
                "picked_runs": len(picked_set),
                "added_runs": len(added_set),
                "pick_rate": round(pick_rate, 4),
                "win_rate": round(win_rate, 4) if win_rate is not None else None,
                "avg_upgrade_level": avg_upgrade,
                "pct_upgraded": pct_upgraded,
            }
        )

    results.sort(key=lambda x: x["offered_runs"], reverse=True)
    return results


# ── Relics ────────────────────────────────────────────────────────────────────

def compute_relics(
    runs: List[Dict[str, Any]],
    known_relics: Optional[List[str]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Compute per-relic stats, split into 'choice' and 'forced' categories.

    Choice relics (shop / ancient): pick rate is meaningful.
    Forced relics (treasure / elite / etc.): only track win-rate-with-relic.

    If *known_relics* is provided, every relic in that list is included in the
    output.  Relics that never appeared in any run are added to the forced list
    with all-zero counts (since their source category is unknown, the forced
    list—which tracks acquisition rather than offers—is the appropriate home).
    """
    # For choice relics: offered/picked per run
    choice_offered: Dict[str, Set[str]] = defaultdict(set)
    choice_picked: Dict[str, Set[str]] = defaultdict(set)
    choice_win: Dict[str, Set[str]] = defaultdict(set)
    choice_sources: Dict[str, Set[str]] = defaultdict(set)

    # For forced relics: acquired per run, win per run
    forced_acquired: Dict[str, Set[str]] = defaultdict(set)
    forced_win: Dict[str, Set[str]] = defaultdict(set)
    forced_sources: Dict[str, Set[str]] = defaultdict(set)

    for run in runs:
        run_id = get_run_id(run)
        run_win = run.get("win", False)

        for event in extract_relic_events(run):
            relic = event["relic"]
            source = event["source"]
            picked = event["picked"]

            if source in CHOICE_SOURCES:
                choice_offered[relic].add(run_id)
                choice_sources[relic].add(source)
                if picked:
                    choice_picked[relic].add(run_id)
                    if run_win:
                        choice_win[relic].add(run_id)
            else:
                if picked:
                    forced_acquired[relic].add(run_id)
                    forced_sources[relic].add(source)
                    if run_win:
                        forced_win[relic].add(run_id)

    choice_list: List[Dict[str, Any]] = []
    for relic, offered_set in choice_offered.items():
        picked_set = choice_picked.get(relic, set())
        won_set = choice_win.get(relic, set())
        pick_rate = len(picked_set) / len(offered_set) if offered_set else 0.0
        win_rate = len(won_set) / len(picked_set) if picked_set else None
        choice_list.append(
            {
                "relic": relic,
                "source": sorted(choice_sources[relic]),
                "offered_runs": len(offered_set),
                "picked_runs": len(picked_set),
                "pick_rate": round(pick_rate, 4),
                "win_rate": round(win_rate, 4) if win_rate is not None else None,
            }
        )
    choice_list.sort(key=lambda x: x["offered_runs"], reverse=True)

    forced_list: List[Dict[str, Any]] = []
    for relic, acquired_set in forced_acquired.items():
        won_set = forced_win.get(relic, set())
        win_rate = len(won_set) / len(acquired_set) if acquired_set else None
        forced_list.append(
            {
                "relic": relic,
                "source": sorted(forced_sources.get(relic, {"forced"})),
                "acquired_runs": len(acquired_set),
                "win_rate": round(win_rate, 4) if win_rate is not None else None,
            }
        )
    forced_list.sort(key=lambda x: x["acquired_runs"], reverse=True)

    # Add zero-value entries for known relics not seen in any run data
    if known_relics:
        seen_relics: Set[str] = set(choice_offered.keys()) | set(forced_acquired.keys())
        for relic in known_relics:
            if relic not in seen_relics:
                forced_list.append(
                    {
                        "relic": relic,
                        "source": [],
                        "acquired_runs": 0,
                        "win_rate": None,
                    }
                )

    return {"choice": choice_list, "forced": forced_list}


# ── Encounters ────────────────────────────────────────────────────────────────

def compute_encounters(runs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Compute avg damage taken, encounter count, and death rate per named encounter."""
    enc_damage: Dict[str, List[int]] = defaultdict(list)
    enc_turns: Dict[str, List[int]] = defaultdict(list)
    enc_type: Dict[str, str] = {}
    enc_deaths: Dict[str, int] = defaultdict(int)
    enc_runs_faced: Dict[str, Set[str]] = defaultdict(set)

    for run in runs:
        run_id = get_run_id(run)
        killed_by = run_killed_by(run)
        for enc in extract_encounters(run):
            name = enc["name"]
            enc_damage[name].append(enc["damage_taken"])
            enc_type[name] = enc["type"]
            if enc["turns"] is not None:
                enc_turns[name].append(enc["turns"])
            enc_runs_faced[name].add(run_id)
        if killed_by:
            enc_deaths[killed_by] += 1

    results: List[Dict[str, Any]] = []
    for name, damages in enc_damage.items():
        turns = enc_turns.get(name, [])
        runs_faced = len(enc_runs_faced.get(name, set()))
        deaths = enc_deaths.get(name, 0)
        results.append(
            {
                "name": name,
                "type": enc_type.get(name, "unknown"),
                "count": len(damages),
                "avg_damage": round(sum(damages) / len(damages), 1),
                "avg_turns": round(sum(turns) / len(turns), 1) if turns else None,
                "deaths": deaths,
                "death_rate": round(deaths / runs_faced, 4) if runs_faced else 0.0,
            }
        )
    results.sort(key=lambda x: x["count"], reverse=True)
    return results


# ── Rest sites ────────────────────────────────────────────────────────────────

def compute_rest_sites(runs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Compute rest-site choice frequency and win rate."""
    choice_runs: Dict[str, Set[str]] = defaultdict(set)
    choice_win: Dict[str, Set[str]] = defaultdict(set)
    choice_healed: Dict[str, List[int]] = defaultdict(list)

    for run in runs:
        run_id = get_run_id(run)
        run_win = run.get("win", False)
        for event in extract_rest_sites(run):
            choice = event["choice"]
            choice_runs[choice].add(run_id)
            if run_win:
                choice_win[choice].add(run_id)
            if event["hp_healed"]:
                choice_healed[choice].append(event["hp_healed"])

    total_visits = sum(len(v) for v in choice_runs.values())

    results: List[Dict[str, Any]] = []
    for choice, run_set in choice_runs.items():
        won_set = choice_win.get(choice, set())
        healed = choice_healed.get(choice, [])
        frequency = len(run_set) / total_visits if total_visits else 0.0
        win_rate = len(won_set) / len(run_set) if run_set else None
        results.append(
            {
                "choice": choice,
                "times_used": len(run_set),
                "frequency": round(frequency, 4),
                "win_rate": round(win_rate, 4) if win_rate is not None else None,
                "avg_hp_healed": round(sum(healed) / len(healed), 1) if healed else None,
            }
        )
    results.sort(key=lambda x: x["times_used"], reverse=True)
    return results


# ── Ancients ──────────────────────────────────────────────────────────────────

def compute_ancients(runs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Compute per-ancient stats: encounter count, unique runs, win rate,
    and per-relic pick/win rates for relics offered by that ancient.
    """
    ancient_run_ids: Dict[str, Set[str]] = defaultdict(set)
    ancient_win_ids: Dict[str, Set[str]] = defaultdict(set)
    ancient_encounters: Dict[str, int] = defaultdict(int)

    # relic stats keyed by (ancient_name, relic)
    relic_offered: Dict[str, Dict[str, Set[str]]] = defaultdict(lambda: defaultdict(set))
    relic_picked: Dict[str, Dict[str, Set[str]]] = defaultdict(lambda: defaultdict(set))
    relic_win: Dict[str, Dict[str, Set[str]]] = defaultdict(lambda: defaultdict(set))

    for run in runs:
        run_id = get_run_id(run)
        run_win = run.get("win", False)
        for ev in extract_ancients(run):
            name = ev["name"]
            ancient_run_ids[name].add(run_id)
            ancient_encounters[name] += 1
            if run_win:
                ancient_win_ids[name].add(run_id)
            for opt in ev["ancient_choice"]:
                relic = opt.get("TextKey", "")
                if not relic:
                    continue
                relic_offered[name][relic].add(run_id)
                if opt.get("was_chosen", False):
                    relic_picked[name][relic].add(run_id)
                    if run_win:
                        relic_win[name][relic].add(run_id)

    results: List[Dict[str, Any]] = []
    for name, run_set in ancient_run_ids.items():
        win_set = ancient_win_ids.get(name, set())
        win_rate = len(win_set) / len(run_set) if run_set else None

        relics: List[Dict[str, Any]] = []
        for relic, offered_set in relic_offered[name].items():
            picked_set = relic_picked[name].get(relic, set())
            w_set = relic_win[name].get(relic, set())
            pick_rate = len(picked_set) / len(offered_set) if offered_set else 0.0
            relic_win_rate = len(w_set) / len(picked_set) if picked_set else None
            relics.append({
                "relic": relic,
                "offered_runs": len(offered_set),
                "picked_runs": len(picked_set),
                "pick_rate": round(pick_rate, 4),
                "win_rate": round(relic_win_rate, 4) if relic_win_rate is not None else None,
            })
        relics.sort(key=lambda x: x["offered_runs"], reverse=True)

        results.append({
            "name": name,
            "encounters": ancient_encounters[name],
            "runs": len(run_set),
            "win_rate": round(win_rate, 4) if win_rate is not None else None,
            "relics": relics,
        })

    results.sort(key=lambda x: x["encounters"], reverse=True)
    return results


# ── Events ────────────────────────────────────────────────────────────────────

def compute_events(runs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Compute per-event stats: encounter count, unique runs, win rate,
    and per-option pick counts and win rates (based on the primary/first choice).
    """
    event_run_ids: Dict[str, Set[str]] = defaultdict(set)
    event_win_ids: Dict[str, Set[str]] = defaultdict(set)
    event_encounters: Dict[str, int] = defaultdict(int)

    # option stats keyed by (event_name, option)
    option_runs: Dict[str, Dict[str, Set[str]]] = defaultdict(lambda: defaultdict(set))
    option_win: Dict[str, Dict[str, Set[str]]] = defaultdict(lambda: defaultdict(set))

    for run in runs:
        run_id = get_run_id(run)
        run_win = run.get("win", False)
        for ev in extract_events(run):
            name = ev["name"]
            event_run_ids[name].add(run_id)
            event_encounters[name] += 1
            if run_win:
                event_win_ids[name].add(run_id)
            # Track only the primary (first) choice per visit
            if ev["choices"]:
                primary = ev["choices"][0]
                option_runs[name][primary].add(run_id)
                if run_win:
                    option_win[name][primary].add(run_id)

    results: List[Dict[str, Any]] = []
    for name, run_set in event_run_ids.items():
        win_set = event_win_ids.get(name, set())
        win_rate = len(win_set) / len(run_set) if run_set else None

        options: List[Dict[str, Any]] = []
        for option, opt_run_set in option_runs[name].items():
            opt_win_set = option_win[name].get(option, set())
            opt_win_rate = len(opt_win_set) / len(opt_run_set) if opt_run_set else None
            options.append({
                "option": option,
                "times_chosen": len(opt_run_set),
                "win_rate": round(opt_win_rate, 4) if opt_win_rate is not None else None,
            })
        options.sort(key=lambda x: x["times_chosen"], reverse=True)

        results.append({
            "name": name,
            "encounters": event_encounters[name],
            "runs": len(run_set),
            "win_rate": round(win_rate, 4) if win_rate is not None else None,
            "options": options,
        })

    results.sort(key=lambda x: x["encounters"], reverse=True)
    return results


# ── Individual run list ───────────────────────────────────────────────────────

def compute_runs_list(runs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return a summary row for each run (for the Runs tab table)."""
    from .parser import _strip_prefix

    rows: List[Dict[str, Any]] = []
    for run in runs:
        # Use the final relic list from players[0].relics if available
        # (most accurate — reflects the actual end-of-run state)
        players = run.get("players", [])
        relics: List[str] = []
        if players:
            for r in players[0].get("relics", []):
                relic_id = _strip_prefix(r.get("id", "")) if isinstance(r, dict) else _strip_prefix(str(r))
                if relic_id and relic_id not in relics:
                    relics.append(relic_id)

        # Fallback: collect from relic events if players[0].relics is empty
        if not relics:
            for event in extract_relic_events(run):
                if event["picked"] and event["relic"] not in relics:
                    relics.append(event["relic"])

        rows.append(
            {
                "filename": run.get("_filename", ""),
                "character": get_character(run),
                "win": run.get("win", False),
                "ascension": run.get("ascension", 0),
                "acts_reached": run_acts_reached(run),
                "hp": run_final_hp(run),
                "max_hp": run_final_max_hp(run),
                "gold": run_final_gold(run),
                "deck_size": run_deck_size(run),
                "deck": run_deck(run),
                "total_damage": run_total_damage(run),
                "relics": relics,
                "killed_by": run_killed_by(run),
                "run_time_s": run.get("run_time"),
                "was_abandoned": run.get("was_abandoned", False),
                "seed": run.get("seed"),
            }
        )
    return rows


# ── Individual run detail ─────────────────────────────────────────────────────

def compute_run_detail(run: Dict[str, Any]) -> Dict[str, Any]:
    """Return full detail for a single run (for the run-detail modal)."""
    from .parser import _strip_prefix

    # Reuse compute_runs_list for the summary fields
    summary_rows = compute_runs_list([run])
    summary = summary_rows[0] if summary_rows else {}

    # Map path — one entry per node visited
    map_path: List[List[Dict[str, Any]]] = []
    for act in run.get("map_point_history", []):
        act_nodes: List[Dict[str, Any]] = []
        for node in act:
            node_type: str = node.get("map_point_type", "unknown").lower()
            rooms = node.get("rooms", [])
            room = rooms[0] if rooms else {}
            model_id = room.get("model_id", "")
            name = _strip_prefix(model_id) if model_id else node_type
            ps_list = node.get("player_stats", [])
            ps: Dict[str, Any] = ps_list[0] if ps_list else {}
            act_nodes.append({
                "type": node_type,
                "name": name,
                "hp": ps.get("current_hp"),
                "max_hp": ps.get("max_hp"),
                "gold": ps.get("current_gold"),
                "damage_taken": ps.get("damage_taken") or 0,
            })
        map_path.append(act_nodes)

    return {
        **summary,
        "card_choices": extract_card_choices(run),
        "relic_events": extract_relic_events(run),
        "encounters": extract_encounters(run),
        "rest_sites": extract_rest_sites(run),
        "map_path": map_path,
    }


# ── Diagnostic ────────────────────────────────────────────────────────────────

def compute_diagnostic(all_runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return raw diagnostic info (before any filtering)."""
    total = len(all_runs)
    multiplayer = sum(1 for r in all_runs if not is_solo_run(r))
    abandoned_first_floor = sum(
        1 for r in all_runs if is_abandoned_first_floor(r)
    )
    wins = sum(1 for r in all_runs if r.get("win", False))

    char_counts: Dict[str, int] = defaultdict(int)
    for r in all_runs:
        char_counts[get_character(r)] += 1

    seeds: List[str] = [r.get("seed", "") for r in all_runs]
    dupe_seeds = len(seeds) - len(set(seeds))

    # Schema anomalies: runs missing expected top-level keys
    expected_keys = {
        "players", "win", "ascension", "seed", "run_time",
        "was_abandoned", "map_point_history",
    }
    anomalies: List[str] = []
    for r in all_runs:
        missing = expected_keys - set(r.keys())
        if missing:
            anomalies.append(
                f"{r.get('_filename', '?')} missing: {', '.join(sorted(missing))}"
            )

    return {
        "total_files": total,
        "multiplayer_runs": multiplayer,
        "solo_runs": total - multiplayer,
        "wins": wins,
        "losses": total - multiplayer - wins,
        "abandoned_first_floor": abandoned_first_floor,
        "duplicate_seeds": dupe_seeds,
        "characters": dict(sorted(char_counts.items())),
        "schema_anomalies": anomalies,
    }


# ── Potions ───────────────────────────────────────────────────────────────────

def compute_potions(
    runs: List[Dict[str, Any]],
    known_potions: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Compute per-potion pick rate, usage rate, and win rate.

    For each potion:
    - ``offered_runs``: runs in which the potion was offered at least once.
    - ``picked_runs``: runs in which the potion was picked at least once.
    - ``used_runs``: runs in which the potion was used at least once.
    - ``pick_rate``: picked_runs / offered_runs.
    - ``use_rate``: used_runs / picked_runs.
    - ``win_rate``: win rate across runs where the potion was picked.

    If *known_potions* is provided, every potion in that list is included in
    the output even if it never appeared in any run (those entries show zeros).
    """
    offered_runs: Dict[str, Set[str]] = defaultdict(set)
    picked_runs: Dict[str, Set[str]] = defaultdict(set)
    used_runs: Dict[str, Set[str]] = defaultdict(set)
    win_runs: Dict[str, Set[str]] = defaultdict(set)

    for run in runs:
        run_id = get_run_id(run)
        run_win = run.get("win", False)
        for event in extract_potion_events(run):
            potion = event["potion"]
            if event["event"] == "picked":
                offered_runs[potion].add(run_id)
                if event["was_picked"]:
                    picked_runs[potion].add(run_id)
                    if run_win:
                        win_runs[potion].add(run_id)
            elif event["event"] == "used":
                used_runs[potion].add(run_id)

    # Collect all potion IDs seen in run data plus any provided known IDs
    all_potions: Set[str] = set(offered_runs.keys()) | set(used_runs.keys())
    if known_potions:
        all_potions.update(known_potions)

    results: List[Dict[str, Any]] = []
    for potion in all_potions:
        off_set = offered_runs.get(potion, set())
        pick_set = picked_runs.get(potion, set())
        use_set = used_runs.get(potion, set())
        won_set = win_runs.get(potion, set())

        pick_rate = len(pick_set) / len(off_set) if off_set else None
        use_rate = len(use_set) / len(pick_set) if pick_set else None
        win_rate = len(won_set) / len(pick_set) if pick_set else None

        results.append({
            "potion": potion,
            "offered_runs": len(off_set),
            "picked_runs": len(pick_set),
            "used_runs": len(use_set),
            "pick_rate": round(pick_rate, 4) if pick_rate is not None else None,
            "use_rate": round(use_rate, 4) if use_rate is not None else None,
            "win_rate": round(win_rate, 4) if win_rate is not None else None,
        })

    results.sort(key=lambda x: x["offered_runs"], reverse=True)
    return results


# ── Shop stats ────────────────────────────────────────────────────────────────

def compute_shop_stats(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute statistics about shop purchases and card removals.

    Returns:
        ``{
            "overview": {
                "total_shop_visits": int,
                "runs_with_removal": int,
                "pct_runs_with_removal": float,
                "avg_removals_per_run": float,
            },
            "card_removals": [{"card": str, "removed_runs": int, "total_removals": int, "win_rate": float|None}, ...],
            "cards_purchased": [{"card": str, "is_colorless": bool, "purchased_runs": int, "win_rate": float|None}, ...],
            "relics_purchased": [{"relic": str, "purchased_runs": int, "win_rate": float|None}, ...],
            "potions_purchased": [{"potion": str, "purchased_runs": int, "win_rate": float|None}, ...],
        }``
    """
    # Per-card removal tracking
    removal_runs: Dict[str, Set[str]] = defaultdict(set)
    removal_counts: Dict[str, int] = defaultdict(int)
    removal_win: Dict[str, Set[str]] = defaultdict(set)

    # Per-card purchase tracking
    card_purchase_runs: Dict[str, Set[str]] = defaultdict(set)
    card_is_colorless: Dict[str, bool] = {}
    card_purchase_win: Dict[str, Set[str]] = defaultdict(set)

    # Per-relic purchase tracking
    relic_purchase_runs: Dict[str, Set[str]] = defaultdict(set)
    relic_purchase_win: Dict[str, Set[str]] = defaultdict(set)

    # Per-potion purchase tracking
    potion_purchase_runs: Dict[str, Set[str]] = defaultdict(set)
    potion_purchase_win: Dict[str, Set[str]] = defaultdict(set)

    total_shop_visits = 0
    runs_with_removal: Set[str] = set()
    total_removals_by_run: Dict[str, int] = defaultdict(int)

    for run in runs:
        run_id = get_run_id(run)
        run_win = run.get("win", False)

        for shop in extract_shop_events(run):
            total_shop_visits += 1

            for entry in shop["cards_purchased"]:
                card = entry["card"]
                card_purchase_runs[card].add(run_id)
                card_is_colorless[card] = entry["is_colorless"]
                if run_win:
                    card_purchase_win[card].add(run_id)

            for relic in shop["relics_purchased"]:
                relic_purchase_runs[relic].add(run_id)
                if run_win:
                    relic_purchase_win[relic].add(run_id)

            for potion in shop["potions_purchased"]:
                potion_purchase_runs[potion].add(run_id)
                if run_win:
                    potion_purchase_win[potion].add(run_id)

            for card in shop["cards_removed"]:
                removal_runs[card].add(run_id)
                removal_counts[card] += 1
                total_removals_by_run[run_id] += 1
                if run_win:
                    removal_win[card].add(run_id)
                runs_with_removal.add(run_id)

    total_runs = len(runs)
    runs_with_removal_count = len(runs_with_removal)
    pct_removal = runs_with_removal_count / total_runs if total_runs else 0.0
    avg_removals = (
        sum(total_removals_by_run.values()) / total_runs if total_runs else 0.0
    )

    # Build card_removals list
    card_removals: List[Dict[str, Any]] = []
    for card, run_set in removal_runs.items():
        won_set = removal_win.get(card, set())
        win_rate = len(won_set) / len(run_set) if run_set else None
        card_removals.append({
            "card": card,
            "removed_runs": len(run_set),
            "total_removals": removal_counts[card],
            "win_rate": round(win_rate, 4) if win_rate is not None else None,
        })
    card_removals.sort(key=lambda x: x["removed_runs"], reverse=True)

    # Build cards_purchased list
    cards_purchased: List[Dict[str, Any]] = []
    for card, run_set in card_purchase_runs.items():
        won_set = card_purchase_win.get(card, set())
        win_rate = len(won_set) / len(run_set) if run_set else None
        cards_purchased.append({
            "card": card,
            "is_colorless": card_is_colorless.get(card, False),
            "purchased_runs": len(run_set),
            "win_rate": round(win_rate, 4) if win_rate is not None else None,
        })
    cards_purchased.sort(key=lambda x: x["purchased_runs"], reverse=True)

    # Build relics_purchased list
    relics_purchased: List[Dict[str, Any]] = []
    for relic, run_set in relic_purchase_runs.items():
        won_set = relic_purchase_win.get(relic, set())
        win_rate = len(won_set) / len(run_set) if run_set else None
        relics_purchased.append({
            "relic": relic,
            "purchased_runs": len(run_set),
            "win_rate": round(win_rate, 4) if win_rate is not None else None,
        })
    relics_purchased.sort(key=lambda x: x["purchased_runs"], reverse=True)

    # Build potions_purchased list
    potions_purchased: List[Dict[str, Any]] = []
    for potion, run_set in potion_purchase_runs.items():
        won_set = potion_purchase_win.get(potion, set())
        win_rate = len(won_set) / len(run_set) if run_set else None
        potions_purchased.append({
            "potion": potion,
            "purchased_runs": len(run_set),
            "win_rate": round(win_rate, 4) if win_rate is not None else None,
        })
    potions_purchased.sort(key=lambda x: x["purchased_runs"], reverse=True)

    return {
        "overview": {
            "total_shop_visits": total_shop_visits,
            "runs_with_removal": runs_with_removal_count,
            "pct_runs_with_removal": round(pct_removal, 4),
            "avg_removals_per_run": round(avg_removals, 2),
        },
        "card_removals": card_removals,
        "cards_purchased": cards_purchased,
        "relics_purchased": relics_purchased,
        "potions_purchased": potions_purchased,
    }
