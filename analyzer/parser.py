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


# ── ID normalization helpers ──────────────────────────────────────────────────

def _strip_prefix(value: str) -> str:
    """Strip the STS2 type prefix from an ID string.

    e.g. ``"CARD.SETUP_STRIKE"`` → ``"SETUP_STRIKE"``
         ``"RELIC.BURNING_BLOOD"`` → ``"BURNING_BLOOD"``
         ``"ENCOUNTER.NIBBITS_WEAK"`` → ``"NIBBITS_WEAK"``
    """
    if "." in value:
        return value.split(".", 1)[1]
    return value


def _is_none(value: Optional[str]) -> bool:
    """Return True for null/NONE.NONE values."""
    return not value or value.upper() in ("NONE.NONE", "NONE", "")


# ── File loading ──────────────────────────────────────────────────────────────

def load_run_files(history_path: str) -> List[Dict[str, Any]]:
    """Recursively load all *.run files from *history_path*.

    Skips ``.backup`` files.
    Each run dict has an extra ``_filename`` key added for debugging.
    Files that fail to parse are skipped with a warning.
    """
    runs: List[Dict[str, Any]] = []
    path = Path(history_path)
    if not path.exists():
        return runs
    for f in sorted(path.rglob("*.run")):
        # Skip backup files
        if f.name.endswith(".run.backup") or f.suffix != ".run":
            continue
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
    """Return the character name, e.g. ``'IRONCLAD'`` or ``'NECROBINDER'``."""
    players = run.get("players", [])
    if not players:
        return "UNKNOWN"
    raw = players[0].get("character", "UNKNOWN")
    # "CHARACTER.IRONCLAD" → "IRONCLAD"
    return _strip_prefix(raw)


def get_run_id(run: Dict[str, Any]) -> str:
    """Stable identifier for deduplication (seed preferred, filename fallback)."""
    seed = run.get("seed")
    if seed:
        return str(seed)
    return run.get("_filename", str(id(run)))


# ── Node iteration ────────────────────────────────────────────────────────────

def iter_nodes(run: Dict[str, Any]) -> Iterator[Tuple[str, Dict[str, Any], Dict[str, Any]]]:
    """Yield ``(node_type, player_stats_dict, node_dict)`` for every map node."""
    for act in run.get("map_point_history", []):
        for node in act:
            node_type: str = node.get("map_point_type", "unknown").lower()
            ps_list = node.get("player_stats", [])
            player_stats: Dict[str, Any] = ps_list[0] if ps_list else {}
            yield node_type, player_stats, node


# ── Card choices ──────────────────────────────────────────────────────────────

def extract_card_choices(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return a list of card-choice events across the whole run.

    Real schema: ``card_choices`` is a flat list of
    ``{"card": {"id": "CARD.XXX", ...}, "was_picked": bool}``
    All entries on a node form one offering; the one with ``was_picked=True``
    is what the player chose.

    Returns events of the form:
        ``{"offered": ["SETUP_STRIKE", "TREMBLE", ...], "picked": str | None}``
    """
    result: List[Dict[str, Any]] = []
    for _node_type, stats, _node in iter_nodes(run):
        choices = stats.get("card_choices", [])
        if not choices:
            continue
        offered: List[str] = []
        picked: Optional[str] = None
        for entry in choices:
            card = entry.get("card", {})
            card_id = _strip_prefix(card.get("id", "")) if isinstance(card, dict) else ""
            if card_id:
                offered.append(card_id)
            if entry.get("was_picked", False) and card_id:
                picked = card_id
        if offered:
            result.append({"offered": offered, "picked": picked})
    return result


# ── Relic choices ─────────────────────────────────────────────────────────────

def extract_relic_events(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return all relic acquisition events, correctly handling ancient nodes.

    Real schema for relic_choices:
        ``[{"choice": "RELIC.XXX", "was_picked": bool}, ...]``
        All entries form one offering; ``was_picked=True`` is the chosen one.

    Real schema for ancient_choice:
        ``[{"TextKey": "RELIC_NAME", "was_chosen": bool}, ...]``
        Use this for ancient nodes instead of relic_choices.

    Returns events of the form:
        ``{"relic": str, "source": str, "offered": [...], "picked": bool}``
    """
    result: List[Dict[str, Any]] = []

    for node_type, stats, _node in iter_nodes(run):
        if node_type == "ancient":
            # ── Ancient node: use ancient_choice, skip relic_choices ──────────
            ancient = stats.get("ancient_choice", [])
            if not ancient or not isinstance(ancient, list):
                continue
            offered: List[str] = []
            for opt in ancient:
                relic_name = opt.get("TextKey", "")
                if relic_name:
                    offered.append(relic_name)
            for opt in ancient:
                relic_name = opt.get("TextKey", "")
                if relic_name:
                    result.append({
                        "relic": relic_name,
                        "source": "ancient",
                        "offered": offered,
                        "picked": bool(opt.get("was_chosen", False)),
                    })
        else:
            # ── Non-ancient node: use relic_choices ──────────────────────────
            choices = stats.get("relic_choices", [])
            if not choices:
                continue
            offered = [
                _strip_prefix(c.get("choice", ""))
                for c in choices
                if c.get("choice")
            ]
            for entry in choices:
                relic_raw = entry.get("choice", "")
                relic_id = _strip_prefix(relic_raw)
                if relic_id:
                    result.append({
                        "relic": relic_id,
                        "source": node_type,
                        "offered": offered,
                        "picked": bool(entry.get("was_picked", False)),
                    })

    return result


# ── Encounter data ────────────────────────────────────────────────────────────

def extract_encounters(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return combat nodes with damage and turn info.

    Real schema: encounter name comes from ``node["rooms"][0]["model_id"]``,
    turns from ``node["rooms"][0]["turns_taken"]``.
    """
    result: List[Dict[str, Any]] = []
    for node_type, stats, node in iter_nodes(run):
        if node_type not in ("monster", "elite", "boss"):
            continue
        rooms = node.get("rooms", [])
        room = rooms[0] if rooms else {}
        model_id = room.get("model_id", "")
        encounter_name = _strip_prefix(model_id) if model_id else f"[{node_type}]"
        turns = room.get("turns_taken")
        result.append({
            "name": encounter_name,
            "type": node_type,
            "damage_taken": stats.get("damage_taken", 0) or 0,
            "turns": turns,
        })
    return result


# ── Rest site data ────────────────────────────────────────────────────────────

def extract_rest_sites(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return rest-site events with choice and HP healed.

    Real schema: ``rest_site_choices`` is a list of strings like ``["SMITH"]``.
    Also handles dict entries ``{"rest_site_choice": "SMITH"}`` for robustness.
    """
    result: List[Dict[str, Any]] = []
    for node_type, stats, _node in iter_nodes(run):
        if node_type != "rest_site":
            continue
        choices = stats.get("rest_site_choices", [])
        hp_healed = stats.get("hp_healed", 0) or 0
        if choices:
            for choice in choices:
                if isinstance(choice, dict):
                    # e.g. {"rest_site_choice": "SMITH"}
                    choice_name = (
                        choice.get("rest_site_choice")
                        or choice.get("choice")
                        or choice.get("type")
                        or "UNKNOWN"
                    )
                else:
                    choice_name = str(choice)
                result.append({
                    "choice": str(choice_name).upper(),
                    "hp_healed": hp_healed,
                })
        else:
            result.append({"choice": "UNKNOWN", "hp_healed": hp_healed})
    return result


# ── Ancient data ─────────────────────────────────────────────────────────────

def extract_ancients(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return ancient visit events with the ancient name and relic choices.

    Each entry has:
        ``{"name": str, "ancient_choice": [...]}``
    where ``name`` is the stripped model_id (e.g. ``"NEOW"``, ``"PAEL"``)
    and ``ancient_choice`` is the raw list from ``player_stats``.
    """
    result: List[Dict[str, Any]] = []
    for node_type, stats, node in iter_nodes(run):
        if node_type != "ancient":
            continue
        rooms = node.get("rooms", [])
        room = rooms[0] if rooms else {}
        model_id = room.get("model_id", "")
        ancient_name = _strip_prefix(model_id) if model_id else "UNKNOWN"
        ancient_choice = stats.get("ancient_choice") or []
        result.append({
            "name": ancient_name,
            "ancient_choice": ancient_choice,
        })
    return result


# ── Run summary helpers ───────────────────────────────────────────────────────

def run_total_damage(run: Dict[str, Any]) -> int:
    return sum(
        (stats.get("damage_taken") or 0)
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
    """Return the final deck size using the deck list from players[0]."""
    players = run.get("players", [])
    if players:
        deck = players[0].get("deck", [])
        if deck is not None:
            return len(deck)
    return 0


def run_deck(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return the final deck as a list of card objects with enchantment info.

    Each entry has:
        ``{"id": "BASH", "upgrade": int, "enchantment": str | None, "enchant_amount": int | None}``
    """
    players = run.get("players", [])
    if not players:
        return []
    raw_deck = players[0].get("deck") or []
    result: List[Dict[str, Any]] = []
    for card in raw_deck:
        if not isinstance(card, dict):
            continue
        card_id = _strip_prefix(card.get("id", ""))
        upgrade = card.get("current_upgrade_level", 0)
        enc = card.get("enchantment")
        enchantment: Optional[str] = None
        enchant_amount: Optional[int] = None
        if isinstance(enc, dict):
            enc_id = enc.get("id", "")
            enchantment = _strip_prefix(enc_id) if enc_id else None
            enchant_amount = enc.get("amount")
        result.append({
            "id": card_id,
            "upgrade": upgrade,
            "enchantment": enchantment,
            "enchant_amount": enchant_amount,
        })
    return result


def run_acts_reached(run: Dict[str, Any]) -> int:
    return len(run.get("map_point_history", []))


def run_killed_by(run: Dict[str, Any]) -> Optional[str]:
    """Return a cleaned killed-by string, or None for wins/none."""
    enc = run.get("killed_by_encounter")
    if enc and not _is_none(enc):
        return _strip_prefix(enc)
    ev = run.get("killed_by_event")
    if ev and not _is_none(ev):
        return _strip_prefix(ev)
    return None

