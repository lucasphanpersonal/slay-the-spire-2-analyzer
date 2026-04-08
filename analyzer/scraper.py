"""analyzer/scraper.py — Scrape and cache card art images from sts2.untapped.gg (with wiki fallback)."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Set

# Primary source: sts2.untapped.gg card pages
UNTAPPED_BASE = "https://sts2.untapped.gg"
UNTAPPED_CARDS_PATH = "/en/cards"

# Fallback: STS2 wiki
WIKI_API = "https://slaythespire.wiki.gg/api.php"
WIKI_BASE = "https://slaythespire.wiki.gg"

# Namespace prefix used for STS2 card pages on the wiki
STS2_NAMESPACE = "Slay_the_Spire_2"

# Delay between requests (seconds)
REQUEST_DELAY = 0.3

# User-agent header for all requests
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


def card_id_to_slug(card_id: str) -> str:
    """Convert a card ID to an untapped.gg URL slug.

    Examples:
        ALL_FOR_ONE   → all-for-one
        BASH          → bash
        SETUP_STRIKE  → setup-strike
    """
    return card_id.lower().replace("_", "-")


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


# ── untapped.gg image resolution ─────────────────────────────────────────────

def _fetch_image_from_untapped(card_id: str) -> Optional[str]:
    """Fetch the untapped.gg card page for *card_id* and extract the card image URL.

    Tries, in order:
    1. The ``og:image`` Open Graph meta tag (present in SSR HTML for SEO).
    2. Embedded ``__NEXT_DATA__`` JSON (Next.js server-side props).
    3. Any ``<img>`` src whose URL contains the card slug.

    Returns the absolute image URL, or None if not found.
    """
    slug = card_id_to_slug(card_id)
    page_url = f"{UNTAPPED_BASE}{UNTAPPED_CARDS_PATH}/{slug}"
    html_bytes = _get(page_url)
    if not html_bytes:
        return None

    try:
        html = html_bytes.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return None

    # 1. Open Graph og:image (most reliable — server-rendered for SEO)
    # Handle both attribute orders: property first or content first
    og_match = re.search(
        r'<meta[^>]+(?:'
        r'property=["\']og:image["\'][^>]+content=["\'](https?://[^"\']+)["\']'
        r'|content=["\'](https?://[^"\']+)["\'][^>]+property=["\']og:image["\']'
        r')',
        html,
        re.IGNORECASE,
    )
    if og_match:
        return og_match.group(1) or og_match.group(2)

    # 2. Next.js __NEXT_DATA__ JSON blob
    next_match = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html,
        re.DOTALL | re.IGNORECASE,
    )
    if next_match:
        try:
            next_data = json.loads(next_match.group(1))
            # Walk the whole JSON looking for image URLs containing the slug
            next_json_str = json.dumps(next_data)
            img_matches = re.findall(r'https?://[^\s"\'<>]+(?:\.png|\.jpg|\.jpeg|\.webp|\.gif)', next_json_str, re.IGNORECASE)
            for url in img_matches:
                if slug in url.lower():
                    return url
        except Exception:  # noqa: BLE001
            pass

    # 3. Any <img src="..."> whose URL contains the card slug
    img_matches = re.findall(
        r'<img[^>]+src=["\'](https?://[^"\']+)["\']',
        html,
        re.IGNORECASE,
    )
    for src in img_matches:
        if slug in src.lower():
            return src

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


def _get_images_from_card_page(card_id: str) -> List[str]:
    """Query the STS2 wiki card page and return its list of ``File:`` titles.

    The card page is looked up as ``Slay_the_Spire_2:<DisplayName>``.
    Returns a list of ``File:`` titles (e.g. ``["File:All_for_One.png"]``),
    or an empty list if the page is not found.
    """
    display = card_display_name(card_id)
    page_title = f"{STS2_NAMESPACE}:{display.replace(' ', '_')}"
    params = urllib.parse.urlencode({
        "action": "query",
        "titles": page_title,
        "prop": "images",
        "imlimit": "50",
        "format": "json",
    })
    data = _get_json(f"{WIKI_API}?{params}")
    if not data:
        return []
    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        if page.get("missing") is not None:
            return []
        return [img["title"] for img in page.get("images", []) if "title" in img]
    return []


def _candidate_file_titles(card_id: str) -> List[str]:
    """Return candidate MediaWiki ``File:`` titles to try for *card_id*.

    First tries images listed on the card's ``Slay_the_Spire_2:`` wiki page,
    then falls back to guessed filenames derived from the display name.
    """
    display = card_display_name(card_id)
    display_underscored = display.replace(" ", "_")

    # Prefer .png images found directly on the card's wiki page
    page_images = _get_images_from_card_page(card_id)
    png_images = [t for t in page_images if t.lower().endswith(".png")]

    # Fallback filename guesses
    guesses = [
        f"File:{display_underscored}.png",
        f"File:{display_underscored}.jpg",
        f"File:{display_underscored}_(Card).png",
        f"File:{display_underscored}_(card).png",
    ]

    # Deduplicate while preserving order (page images first)
    seen: Set[str] = set()
    candidates: List[str] = []
    for title in png_images + guesses:
        if title not in seen:
            seen.add(title)
            candidates.append(title)
    return candidates


def fetch_card_image_url(card_id: str) -> Optional[str]:
    """Try to resolve a card image URL for *card_id*.

    First tries sts2.untapped.gg (primary source), then falls back to the
    STS2 wiki (MediaWiki API).

    Returns the direct image URL (e.g., a CDN URL), or None if not found.
    """
    # Primary: untapped.gg card page
    url = _fetch_image_from_untapped(card_id)
    if url:
        return url
    time.sleep(REQUEST_DELAY)

    # Fallback: wiki
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
                print("not found")
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

    print("Fetching images from sts2.untapped.gg (with wiki fallback)…")
    downloaded = scrape_card_images(card_ids, output_dir, verbose=True)

    print(f"\nDone. Downloaded {len(downloaded)}/{len(card_ids)} images → {output_dir}")
