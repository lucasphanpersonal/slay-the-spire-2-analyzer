"""analyzer/parser.py — Load and parse .run files with all STS2 data quirks."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

# ── Node-type classification ──────────────────────────────────────────────────
# "choice" sources: pick rate is meaningful (player chose from options)
CHOICE_SOURCES: frozenset[str] = frozenset({"shop", "ancient"})
# "forced" sources: player received the relic, no meaningful pick rate
FORCED_SOURCES: frozenset[str] = frozenset(
    {"treasure", "elite", "boss", "rest_site", "unknown", "event", "monster"}
)


# ── File loading ──────────────────────────────────────────────────────────────

def load_run_files(history_path: str) -> List[Dict[str, Any]]:
    """Recursively load all *.run files from *history_path*.

    Each run dict has an extra ``_filename`` key added for debugging.
    Files that fail to parse are skipped with a warning.
    """
    runs: List[Dict[str, Any]] = []
    path = Path(history_path)
    if not path.exists():
        return runs
    for f in sorted(path.rglob("*.run")):
        try:
            with open(f, encoding="utf-8") as fp:
                data: Dict[str, Any] = json.load(fp)
            data["_filename"] = f.name
            runs.append(data)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] Could not parse {f.name}: {exc}")
    return runs


# ── Run-level helpers ─────────────────────────────────────────────────────────

def is_solo_run(run: Dict[str, Any]) -> bool:
    return len(run.get("players", [])) == 1


def is_abandoned_first_floor(run: Dict[str, Any]) -> bool:
    """Return True if the run was abandoned on (or before) the first floor.

    Only runs abandoned within the very first floor are considered "meaningless"
    early quits.  Runs abandoned later still contain useful data.
    """
    if not run.get("was_abandoned", False):
        return False
    history = run.get("map_point_history", [])
    if not history:
        return True
    # Count total nodes visited across all acts
    total_nodes = sum(len(act) for act in history)
    return total_nodes <= 1


def get_character(run: Dict[str, Any]) -> str:
    """Return the character name, e.g. ``'IRONCLAD'`` or ``'THE_SILENT'``."""
    players = run.get("players", [])
    if not players:
        return "UNKNOWN"
    raw = players[0].get("character", "UNKNOWN")
    # "CHARACTER.IRONCLAD" → "IRONCLAD"
    return raw.split(".", 1)[1] if "." in raw else raw


def get_run_id(run: Dict[str, Any]) -> str:
    """Stable identifier for deduplication (seed preferred, filename fallback)."""
    seed = run.get("seed")
    if seed:
        return str(seed)
    return run.get("_filename", id(run))


# ── Node iteration ────────────────────────────────────────────────────────────

def iter_nodes(run: Dict[str, Any]) -> Iterator[Tuple[str, Dict[str, Any]]]:
    """Yield ``(node_type, player_stats_dict)`` for every map node in the run."""
    for act in run.get("map_point_history", []):
        for node in act:
            node_type: str = node.get("map_point_type", "unknown").lower()
            ps_list = node.get("player_stats", [])
            player_stats: Dict[str, Any] = ps_list[0] if ps_list else {}
            yield node_type, player_stats, node


# ── Card choices ──────────────────────────────────────────────────────────────

def extract_card_choices(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return a list of card-choice events across the whole run.

    Each event:
        ``{"offered": [...], "picked": str | None}``
    """
    result: List[Dict[str, Any]] = []
    for _node_type, stats, _node in iter_nodes(run):
        for choice in stats.get("card_choices", []):
            offered = (
                choice.get("cards_offered")
                or choice.get("offered")
                or []
            )
            picked = choice.get("card_picked") or choice.get("picked")
            # Normalize card IDs — strip suffix like "_R" added for upgraded cards
            # in the offered list (picked card is treated as-is for display)
            result.append({"offered": offered, "picked": picked})
    return result


# ── Relic choices ─────────────────────────────────────────────────────────────

def extract_relic_events(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return all relic acquisition events, correctly handling ancient nodes.

    Each event:
        ``{"relic": str, "source": str, "offered": [...], "picked": bool}``

    Quirk handled:
        Ancient nodes double-write relics.  We use ``ancient_choice`` for those
        nodes and **skip** ``relic_choices`` to avoid double-counting.
    """
    result: List[Dict[str, Any]] = []

    for node_type, stats, _node in iter_nodes(run):
        if node_type == "ancient":
            # ── Ancient node: use ancient_choice, ignore relic_choices ──────
            ancient = stats.get("ancient_choice")
            if not ancient:
                continue

            if isinstance(ancient, list):
                # Format A: [{"relic": "X", "picked": false}, ...]
                offered = [
                    _relic_id(opt)
                    for opt in ancient
                    if _relic_id(opt)
                ]
                for opt in ancient:
                    rid = _relic_id(opt)
                    if rid:
                        picked = bool(
                            opt.get("picked")
                            or opt.get("selected")
                            or opt.get("was_picked")
                        )
                        result.append(
                            {
                                "relic": rid,
                                "source": "ancient",
                                "offered": offered,
                                "picked": picked,
                            }
                        )
            elif isinstance(ancient, dict):
                # Format B: {"relics_offered": [...], "relic_picked": "X"}
                offered = ancient.get("relics_offered") or ancient.get("offered") or []
                picked_relic = ancient.get("relic_picked") or ancient.get("picked")
                for r in offered:
                    result.append(
                        {
                            "relic": r,
                            "source": "ancient",
                            "offered": offered,
                            "picked": r == picked_relic,
                        }
                    )
        else:
            # ── Non-ancient node: use relic_choices ──────────────────────────
            for choice in stats.get("relic_choices", []):
                offered = (
                    choice.get("relics_offered")
                    or choice.get("offered")
                    or []
                )
                picked_relic = choice.get("relic_picked") or choice.get("picked")
                all_relics = offered if offered else (
                    [picked_relic] if picked_relic else []
                )
                for r in all_relics:
                    if r:
                        result.append(
                            {
                                "relic": r,
                                "source": node_type,
                                "offered": offered,
                                "picked": r == picked_relic,
                            }
                        )

    return result


def _relic_id(opt: Any) -> Optional[str]:
    if isinstance(opt, dict):
        return opt.get("relic") or opt.get("relic_id") or opt.get("id") or opt.get("name")
    if isinstance(opt, str):
        return opt
    return None


# ── Encounter data ────────────────────────────────────────────────────────────

def extract_encounters(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return combat nodes with damage and turn info."""
    result: List[Dict[str, Any]] = []
    for node_type, stats, node in iter_nodes(run):
        if node_type not in ("monster", "elite", "boss"):
            continue
        encounter_name = (
            node.get("encounter_id")
            or node.get("encounter_name")
            or node.get("encounter")
            or node.get("name")
            or f"[{node_type}]"
        )
        result.append(
            {
                "name": encounter_name,
                "type": node_type,
                "damage_taken": stats.get("damage_taken", 0),
                "turns": node.get("turns") or node.get("num_turns"),
            }
        )
    return result


# ── Rest site data ────────────────────────────────────────────────────────────

def extract_rest_sites(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return rest-site events with choice and HP healed."""
    result: List[Dict[str, Any]] = []
    for node_type, stats, _node in iter_nodes(run):
        if node_type != "rest_site":
            continue
        choices = stats.get("rest_site_choices", [])
        hp_healed = stats.get("hp_healed", 0)
        if choices:
            for c in choices:
                choice_name = (
                    c.get("rest_site_choice")
                    or c.get("choice")
                    or c.get("type")
                    or "UNKNOWN"
                )
                result.append(
                    {
                        "choice": str(choice_name).upper(),
                        "hp_healed": hp_healed,
                    }
                )
        else:
            # Node visited but no choice recorded — mark as unknown
            result.append({"choice": "UNKNOWN", "hp_healed": hp_healed})
    return result


# ── Run summary helpers ───────────────────────────────────────────────────────

def run_total_damage(run: Dict[str, Any]) -> int:
    return sum(
        stats.get("damage_taken", 0)
        for _, stats, _ in iter_nodes(run)
    )


def run_final_hp(run: Dict[str, Any]) -> Optional[int]:
    """Return the HP at the last node visited."""
    last_hp = None
    for _, stats, _ in iter_nodes(run):
        hp = stats.get("current_hp")
        if hp is not None:
            last_hp = hp
    return last_hp


def run_final_max_hp(run: Dict[str, Any]) -> Optional[int]:
    last = None
    for _, stats, _ in iter_nodes(run):
        v = stats.get("max_hp")
        if v is not None:
            last = v
    return last


def run_final_gold(run: Dict[str, Any]) -> Optional[int]:
    last = None
    for _, stats, _ in iter_nodes(run):
        v = stats.get("current_gold")
        if v is not None:
            last = v
    return last


def run_deck_size(run: Dict[str, Any]) -> int:
    """Approximate deck size: cards picked minus cards removed."""
    picked = sum(
        1
        for _, stats, _ in iter_nodes(run)
        for c in stats.get("card_choices", [])
        if (c.get("card_picked") or c.get("picked"))
    )
    removed = sum(
        len(stats.get("cards_removed", []))
        for _, stats, _ in iter_nodes(run)
    )
    # STS characters start with ~10 cards
    return max(0, 10 + picked - removed)


def run_acts_reached(run: Dict[str, Any]) -> int:
    return len(run.get("map_point_history", []))
