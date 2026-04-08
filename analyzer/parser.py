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
    All entries on a node form one offering; entries with ``was_picked=True``
    are what the player chose (ancient nodes can yield multiple picks).

    At ancient nodes, cards with ``was_picked=False`` that appear in the
    player's final deck are tracked separately as ``"added"`` — these are
    cards granted by a relic effect (e.g. GLASS_EYE, HEFTY_TABLET) rather
    than a direct player selection.

    Returns events of the form::

        {
            "offered": ["SETUP_STRIKE", "TREMBLE", ...],
            "picked": [str, ...],   # explicitly chosen (was_picked=True)
            "added":  [str, ...],   # relic-granted at ancient, in final deck
        }
    """
    # Pre-compute final deck card IDs for cross-referencing relic-granted cards.
    deck_ids: frozenset[str] = frozenset(
        c["card"] for c in extract_deck_cards(run)
    )

    result: List[Dict[str, Any]] = []
    for node_type, stats, _node in iter_nodes(run):
        choices = stats.get("card_choices", [])
        offered: List[str] = []
        picked: List[str] = []
        added: List[str] = []
        for entry in choices:
            card = entry.get("card", {})
            card_id = _strip_prefix(card.get("id", "")) if isinstance(card, dict) else ""
            if card_id:
                offered.append(card_id)
            if card_id and entry.get("was_picked", False):
                picked.append(card_id)
            elif card_id and node_type == "ancient" and card_id in deck_ids:
                # Relic-granted card: not explicitly chosen but ends up in deck.
                added.append(card_id)
        # At ancient nodes, relics can grant cards directly (e.g. JEWELRY_BOX →
        # APOTHEOSIS).  These appear in ``cards_gained`` rather than
        # ``card_choices``, so they must be collected separately.
        if node_type == "ancient":
            already_tracked = set(picked) | set(added)
            for gained_entry in stats.get("cards_gained", []):
                g_id = _strip_prefix(gained_entry.get("id", ""))
                if g_id and g_id not in already_tracked:
                    added.append(g_id)
                    already_tracked.add(g_id)
        if offered or added:
            result.append({"offered": offered, "picked": picked, "added": added})
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


# ── Potion data ───────────────────────────────────────────────────────────────

def extract_potion_events(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return all potion acquisition and usage events from the run.

    Each entry has:
        ``{"potion": str, "event": "picked"|"used", "source": str}``
    where ``potion`` is the stripped potion ID (e.g. ``"FIRE_POTION"``).
    """
    result: List[Dict[str, Any]] = []

    for node_type, stats, _node in iter_nodes(run):
        for entry in stats.get("potion_choices", []):
            pid = _strip_prefix(entry.get("choice", ""))
            if pid:
                result.append({
                    "potion": pid,
                    "event": "picked",
                    "source": node_type,
                    "was_picked": bool(entry.get("was_picked", False)),
                })
        for pid_raw in stats.get("potion_used", []):
            pid = _strip_prefix(pid_raw) if isinstance(pid_raw, str) else ""
            if pid:
                result.append({
                    "potion": pid,
                    "event": "used",
                    "source": node_type,
                    "was_picked": True,
                })

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


# ── Event data ────────────────────────────────────────────────────────────────

def _parse_event_option(key: str) -> str:
    """Extract the option name from an event choice title key.

    Key format: ``"EVENT_NAME.pages.PAGE.options.OPTION_NAME.title"``
    Falls back to the first segment of the key if the pattern isn't found.
    """
    parts = key.split(".options.")
    if len(parts) >= 2:
        option_part = parts[-1]  # "OPTION_NAME.title"
        return option_part.split(".")[0]
    # Fallback: use the first dot-segment (e.g. "BING_BONG" from "BING_BONG.title")
    return key.split(".")[0]


def extract_events(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return event node visits with the event name and choices made.

    Only ``unknown`` map nodes with an ``EVENT.*`` model_id are included.

    Each entry has:
        ``{"name": str, "choices": [str, ...]}``
    where ``name`` is the stripped model_id (e.g. ``"WELLSPRING"``) and
    ``choices`` is the list of option names chosen (in order, for multi-step
    events).
    """
    result: List[Dict[str, Any]] = []
    for node_type, stats, node in iter_nodes(run):
        if node_type != "unknown":
            continue
        rooms = node.get("rooms", [])
        room = rooms[0] if rooms else {}
        model_id = room.get("model_id", "")
        if not model_id.upper().startswith("EVENT."):
            continue
        event_name = _strip_prefix(model_id)
        raw_choices = stats.get("event_choices") or []
        choices: List[str] = []
        for ec in raw_choices:
            title_key = ec.get("title", {}).get("key", "") if isinstance(ec, dict) else ""
            if title_key:
                choices.append(_parse_event_option(title_key))
        result.append({"name": event_name, "choices": choices})
    return result


# ── Shop data ─────────────────────────────────────────────────────────────────

def extract_shop_events(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return one entry per shop node visit with all items purchased/removed.

    Each entry has:
        ``{
            "cards_purchased": [{"card": str, "is_colorless": bool}, ...],
            "relics_purchased": [str, ...],
            "potions_purchased": [str, ...],
            "cards_removed": [str, ...],
        }``
    where all IDs have their type prefix stripped (e.g. ``"STRIKE_IRONCLAD"``).
    """
    result: List[Dict[str, Any]] = []
    for node_type, stats, _node in iter_nodes(run):
        if node_type != "shop":
            continue

        # colorless cards purchased (subset of cards_gained)
        colorless_ids = {
            _strip_prefix(c) if isinstance(c, str) else ""
            for c in stats.get("bought_colorless", [])
        }

        cards_purchased: List[Dict[str, Any]] = []
        for card in stats.get("cards_gained", []):
            if isinstance(card, dict):
                card_id = _strip_prefix(card.get("id", ""))
            elif isinstance(card, str):
                card_id = _strip_prefix(card)
            else:
                continue
            if card_id:
                cards_purchased.append({
                    "card": card_id,
                    "is_colorless": card_id in colorless_ids,
                })

        relics_purchased = [
            _strip_prefix(r) if isinstance(r, str) else ""
            for r in stats.get("bought_relics", [])
            if r
        ]
        relics_purchased = [r for r in relics_purchased if r]

        potions_purchased = [
            _strip_prefix(p) if isinstance(p, str) else ""
            for p in stats.get("bought_potions", [])
            if p
        ]
        potions_purchased = [p for p in potions_purchased if p]

        cards_removed = [
            _strip_prefix(c.get("id", "")) if isinstance(c, dict) else _strip_prefix(c)
            for c in stats.get("cards_removed", [])
        ]
        cards_removed = [c for c in cards_removed if c]

        result.append({
            "cards_purchased": cards_purchased,
            "relics_purchased": relics_purchased,
            "potions_purchased": potions_purchased,
            "cards_removed": cards_removed,
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


def extract_deck_cards(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return final deck cards with their upgrade levels.

    Returns a list of dicts:
        ``{"card": str, "upgrade_level": int}``
    """
    players = run.get("players", [])
    if not players:
        return []
    deck = players[0].get("deck") or []
    result: List[Dict[str, Any]] = []
    for card in deck:
        card_id = _strip_prefix(card.get("id", "")) if isinstance(card, dict) else ""
        if card_id:
            result.append({
                "card": card_id,
                "upgrade_level": card.get("current_upgrade_level", 0) or 0,
            })
    return result


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

