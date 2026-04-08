"""analyzer/stats.py — Compute statistics from parsed run data."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional, Set

from .parser import (
    CHOICE_SOURCES,
    extract_card_choices,
    extract_encounters,
    extract_relic_events,
    extract_rest_sites,
    get_character,
    get_run_id,
    is_abandoned_first_floor,
    is_solo_run,
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
    }


# ── Cards ─────────────────────────────────────────────────────────────────────

def compute_cards(
    runs: List[Dict[str, Any]],
    min_offered: int = 1,
) -> List[Dict[str, Any]]:
    """Compute per-card pick-rate and win-rate.

    Win rate and pick rate are tracked **per run** (not per offering) to avoid
    inflating stats when a card is offered multiple times in one run.
    """
    # run-level sets: offered_runs[card], picked_runs[card], win_runs[card]
    offered_runs: Dict[str, Set[str]] = defaultdict(set)
    picked_runs: Dict[str, Set[str]] = defaultdict(set)
    win_runs: Dict[str, Set[str]] = defaultdict(set)

    for run in runs:
        run_id = get_run_id(run)
        run_win = run.get("win", False)
        for event in extract_card_choices(run):
            for card in event["offered"]:
                if card:
                    offered_runs[card].add(run_id)
            picked = event.get("picked")
            if picked:
                picked_runs[picked].add(run_id)
                if run_win:
                    win_runs[picked].add(run_id)

    results: List[Dict[str, Any]] = []
    for card, offered_set in offered_runs.items():
        if len(offered_set) < min_offered:
            continue
        picked_set = picked_runs.get(card, set())
        won_set = win_runs.get(card, set())
        pick_rate = len(picked_set) / len(offered_set) if offered_set else 0.0
        win_rate = len(won_set) / len(picked_set) if picked_set else None
        results.append(
            {
                "card": card,
                "offered_runs": len(offered_set),
                "picked_runs": len(picked_set),
                "pick_rate": round(pick_rate, 4),
                "win_rate": round(win_rate, 4) if win_rate is not None else None,
            }
        )

    results.sort(key=lambda x: x["offered_runs"], reverse=True)
    return results


# ── Relics ────────────────────────────────────────────────────────────────────

def compute_relics(runs: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Compute per-relic stats, split into 'choice' and 'forced' categories.

    Choice relics (shop / ancient): pick rate is meaningful.
    Forced relics (treasure / elite / etc.): only track win-rate-with-relic.
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

    return {"choice": choice_list, "forced": forced_list}


# ── Encounters ────────────────────────────────────────────────────────────────

def compute_encounters(runs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Compute avg damage taken and encounter count per named encounter."""
    enc_damage: Dict[str, List[int]] = defaultdict(list)
    enc_turns: Dict[str, List[int]] = defaultdict(list)
    enc_type: Dict[str, str] = {}

    for run in runs:
        for enc in extract_encounters(run):
            name = enc["name"]
            enc_damage[name].append(enc["damage_taken"])
            enc_type[name] = enc["type"]
            if enc["turns"] is not None:
                enc_turns[name].append(enc["turns"])

    results: List[Dict[str, Any]] = []
    for name, damages in enc_damage.items():
        turns = enc_turns.get(name, [])
        results.append(
            {
                "name": name,
                "type": enc_type.get(name, "unknown"),
                "count": len(damages),
                "avg_damage": round(sum(damages) / len(damages), 1),
                "avg_turns": round(sum(turns) / len(turns), 1) if turns else None,
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
