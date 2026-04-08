"""analyzer/stats.py — Compute statistics from parsed run data."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional, Set

from .parser import (
    CHOICE_SOURCES,
    extract_ancients,
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
