"""analyzer/scraper.py — Scrape and cache card art images from the STS2 wiki."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

# Wiki base URL for Slay the Spire (covers STS2 cards)
WIKI_API = "https://slay-the-spire.wiki.gg/api.php"
WIKI_BASE = "https://slay-the-spire.wiki.gg"

# Delay between requests (seconds) to be polite to the wiki
REQUEST_DELAY = 0.3

# User-agent so the wiki knows who is calling
USER_AGENT = "STS2-Run-Analyzer/1.0 (card-image-scraper; github.com/lucasphanpersonal/slay-the-spire-2-analyzer)"


# ── ID → display name helpers ─────────────────────────────────────────────────

# Known suffix words that indicate character-specific starters (kept for title)
_CHARACTER_SUFFIXES: frozenset[str] = frozenset({
    "IRONCLAD", "SILENT", "DEFECT", "WATCHER",
    "NECROBINDER", "HUNTRESS",
})


def card_id_to_display(card_id: str) -> str:
    """Convert a card ID like ``SETUP_STRIKE`` to a display name ``Setup Strike``."""
    words = card_id.replace("_", " ").title()
    return words


def card_id_to_filename(card_id: str) -> str:
    """Convert card ID to the expected local image filename (PNG)."""
    return card_id.lower() + ".png"


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _get(url: str, timeout: int = 10) -> Optional[bytes]:
    """Fetch *url* and return bytes, or None on failure."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception:  # noqa: BLE001
        return None


def _get_json(url: str, timeout: int = 10) -> Optional[dict]:
    """Fetch *url* and return parsed JSON, or None on failure."""
    data = _get(url, timeout=timeout)
    if data is None:
        return None
    try:
        return json.loads(data.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


# ── Wiki image resolution ─────────────────────────────────────────────────────

def _resolve_image_url_via_api(file_title: str) -> Optional[str]:
    """Use the MediaWiki API to get the direct URL for *file_title*.

    *file_title* should be something like ``File:Bash.png``.
    Returns the direct image URL, or None if not found.
    """
    params = urllib.parse.urlencode({
        "action": "query",
        "titles": file_title,
        "prop": "imageinfo",
        "iiprop": "url",
        "format": "json",
    })
    data = _get_json(f"{WIKI_API}?{params}")
    if not data:
        return None
    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        imageinfo = page.get("imageinfo", [])
        if imageinfo:
            return imageinfo[0].get("url")
    return None


def _candidate_file_titles(card_id: str) -> List[str]:
    """Return candidate MediaWiki ``File:`` titles to try for *card_id*."""
    display = card_display_name(card_id)
    display_underscored = display.replace(" ", "_")

    candidates = [
        f"File:{display_underscored}.png",
        f"File:{display_underscored}.jpg",
        f"File:{display_underscored}_(Card).png",
        f"File:{display_underscored}_(card).png",
    ]
    return candidates


def card_display_name(card_id: str) -> str:
    """Convert card ID to a wiki-style display name.

    Examples:
        BASH              → Bash
        SETUP_STRIKE      → Setup Strike
        STRIKE_IRONCLAD   → Strike (Ironclad)
        DEFEND_IRONCLAD   → Defend (Ironclad)
    """
    parts = card_id.split("_")
    # Check if the last word(s) are a character name
    for i in range(len(parts), 0, -1):
        suffix = "_".join(parts[i:]).upper() if i < len(parts) else ""
        if suffix in _CHARACTER_SUFFIXES:
            base = " ".join(p.title() for p in parts[:i])
            char = suffix.title()
            return f"{base} ({char})"
    return " ".join(p.title() for p in parts)


def fetch_card_image_url(card_id: str) -> Optional[str]:
    """Try to resolve a card image URL from the wiki for *card_id*.

    Returns the direct image URL (e.g., a CDN URL), or None if not found.
    """
    for file_title in _candidate_file_titles(card_id):
        url = _resolve_image_url_via_api(file_title)
        if url:
            return url
        time.sleep(REQUEST_DELAY)
    return None


# ── Batch scrape ──────────────────────────────────────────────────────────────

def scrape_card_images(
    card_ids: List[str],
    output_dir: str,
    *,
    skip_existing: bool = True,
    verbose: bool = True,
) -> Dict[str, str]:
    """Download card images for *card_ids* into *output_dir*.

    Parameters
    ----------
    card_ids:
        List of normalized card IDs (e.g. ``["BASH", "SETUP_STRIKE", ...]``).
    output_dir:
        Directory to save PNG images into.
    skip_existing:
        If True, skip cards whose image file already exists.
    verbose:
        Print progress.

    Returns
    -------
    Mapping of ``card_id → local filename`` for successfully downloaded images.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    results: Dict[str, str] = {}
    total = len(card_ids)

    for i, card_id in enumerate(sorted(card_ids), 1):
        filename = card_id_to_filename(card_id)
        dest = out / filename

        if skip_existing and dest.exists():
            results[card_id] = filename
            if verbose:
                print(f"  [{i:>3}/{total}] {card_id:<40} skip (exists)")
            continue

        if verbose:
            print(f"  [{i:>3}/{total}] {card_id:<40} ", end="", flush=True)

        image_url = fetch_card_image_url(card_id)
        if not image_url:
            if verbose:
                print("not found on wiki")
            continue

        image_data = _get(image_url)
        if not image_data:
            if verbose:
                print("download failed")
            continue

        dest.write_bytes(image_data)
        results[card_id] = filename
        if verbose:
            print(f"✓  ({len(image_data) // 1024} KB)")

        time.sleep(REQUEST_DELAY)

    return results


# ── Card ID discovery ─────────────────────────────────────────────────────────

def collect_card_ids_from_runs(history_path: str) -> List[str]:
    """Return all unique card IDs found in *.run files under *history_path*."""
    from .parser import load_run_files, _strip_prefix

    runs = load_run_files(history_path)
    card_ids: set[str] = set()

    for run in runs:
        players = run.get("players", [])
        if not players:
            continue
        for card in players[0].get("deck") or []:
            cid = _strip_prefix(card.get("id", "")) if isinstance(card, dict) else ""
            if cid:
                card_ids.add(cid)

        for act in run.get("map_point_history", []):
            for node in act:
                ps_list = node.get("player_stats", [])
                ps = ps_list[0] if ps_list else {}
                for entry in ps.get("card_choices", []):
                    card = entry.get("card", {})
                    cid = _strip_prefix(card.get("id", "")) if isinstance(card, dict) else ""
                    if cid:
                        card_ids.add(cid)

    return sorted(card_ids)


# ── CLI entry point ───────────────────────────────────────────────────────────

def run_scrape(history_path: str, output_dir: str) -> None:
    """CLI entry: discover cards, download images, report results."""
    print(f"\n⚔  STS2 Card Image Scraper")
    print(f"   History path : {history_path}")
    print(f"   Output dir   : {output_dir}\n")

    print("Discovering card IDs from run files…")
    card_ids = collect_card_ids_from_runs(history_path)
    print(f"Found {len(card_ids)} unique card IDs.\n")

    print("Fetching images from the wiki…")
    downloaded = scrape_card_images(card_ids, output_dir, verbose=True)

    print(f"\nDone. Downloaded {len(downloaded)}/{len(card_ids)} images → {output_dir}")
