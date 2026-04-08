"""analyzer/scraper.py — Scrape and cache card, relic, and potion art images from sts2.untapped.gg (with wiki fallback)."""

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

# Primary source: sts2.untapped.gg card/relic/potion pages
UNTAPPED_BASE = "https://sts2.untapped.gg"
UNTAPPED_CARDS_PATH = "/en/cards"
UNTAPPED_RELICS_PATH = "/en/relics"
UNTAPPED_POTIONS_PATH = "/en/potions"

# Fallback: STS2 wiki
WIKI_API = "https://slaythespire.wiki.gg/api.php"
WIKI_BASE = "https://slaythespire.wiki.gg"

# Namespace prefix used for STS2 pages on the wiki
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


def relic_id_to_slug(relic_id: str) -> str:
    """Convert a relic ID to an untapped.gg URL slug (same convention as cards)."""
    return relic_id.lower().replace("_", "-")


def potion_id_to_slug(potion_id: str) -> str:
    """Convert a potion ID to an untapped.gg URL slug (same convention as cards)."""
    return potion_id.lower().replace("_", "-")


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
    return _fetch_image_from_untapped_page(f"{UNTAPPED_CARDS_PATH}/{slug}", slug)


def _fetch_image_from_untapped_page(page_path: str, slug: str) -> Optional[str]:
    """Generic helper: fetch an untapped.gg page and extract the primary image URL.

    Tries, in order:
    1. The ``og:image`` Open Graph meta tag (present in SSR HTML for SEO).
    2. Embedded ``__NEXT_DATA__`` JSON (Next.js server-side props).
    3. Any ``<img>`` src whose URL contains the slug.

    Returns the absolute image URL, or None if not found.
    """
    page_url = f"{UNTAPPED_BASE}{page_path}"
    html_bytes = _get(page_url)
    if not html_bytes:
        return None

    try:
        html = html_bytes.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return None

    # 1. Open Graph og:image
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
            next_json_str = json.dumps(next_data)
            img_matches = re.findall(r'https?://[^\s"\'<>]+(?:\.png|\.jpg|\.jpeg|\.webp|\.gif)', next_json_str, re.IGNORECASE)
            for url in img_matches:
                if slug in url.lower():
                    return url
        except Exception:  # noqa: BLE001
            pass

    # 3. Any <img src="..."> whose URL contains the slug
    img_matches = re.findall(
        r'<img[^>]+src=["\'](https?://[^"\']+)["\']',
        html,
        re.IGNORECASE,
    )
    for src in img_matches:
        if slug in src.lower():
            return src

    return None


def _fetch_relic_image_from_untapped(relic_id: str) -> Optional[str]:
    """Fetch the untapped.gg relic page for *relic_id* and extract the relic image URL."""
    slug = relic_id_to_slug(relic_id)
    return _fetch_image_from_untapped_page(f"{UNTAPPED_RELICS_PATH}/{slug}", slug)


def _fetch_potion_image_from_untapped(potion_id: str) -> Optional[str]:
    """Fetch the untapped.gg potion page for *potion_id* and extract the potion image URL."""
    slug = potion_id_to_slug(potion_id)
    return _fetch_image_from_untapped_page(f"{UNTAPPED_POTIONS_PATH}/{slug}", slug)


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


def _candidate_wiki_file_titles(item_id: str) -> List[str]:
    """Return candidate MediaWiki ``File:`` titles for a relic or potion *item_id*.

    Generates filename guesses from the display name (title-cased words).
    """
    display = " ".join(p.title() for p in item_id.split("_"))
    display_underscored = display.replace(" ", "_")
    return [
        f"File:{display_underscored}.png",
        f"File:{display_underscored}.jpg",
    ]


def fetch_relic_image_url(relic_id: str) -> Optional[str]:
    """Try to resolve a relic image URL for *relic_id*.

    First tries sts2.untapped.gg, then falls back to the STS2 wiki.
    """
    url = _fetch_relic_image_from_untapped(relic_id)
    if url:
        return url
    time.sleep(REQUEST_DELAY)

    for file_title in _candidate_wiki_file_titles(relic_id):
        url = _resolve_image_url_via_api(file_title)
        if url:
            return url
        time.sleep(REQUEST_DELAY)
    return None


def fetch_potion_image_url(potion_id: str) -> Optional[str]:
    """Try to resolve a potion image URL for *potion_id*.

    First tries sts2.untapped.gg, then falls back to the STS2 wiki.
    """
    url = _fetch_potion_image_from_untapped(potion_id)
    if url:
        return url
    time.sleep(REQUEST_DELAY)

    for file_title in _candidate_wiki_file_titles(potion_id):
        url = _resolve_image_url_via_api(file_title)
        if url:
            return url
        time.sleep(REQUEST_DELAY)
    return None


# ── Batch scrape ──────────────────────────────────────────────────────────────

def _scrape_images_generic(
    item_ids: List[str],
    output_dir: str,
    fetch_url_fn,
    *,
    skip_existing: bool = True,
    verbose: bool = True,
) -> Dict[str, str]:
    """Download images for *item_ids* into *output_dir* using *fetch_url_fn*.

    Returns a mapping of ``item_id → local filename`` for successfully downloaded images.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    results: Dict[str, str] = {}
    total = len(item_ids)

    for i, item_id in enumerate(sorted(item_ids), 1):
        filename = item_id.lower() + ".png"
        dest = out / filename

        if skip_existing and dest.exists():
            results[item_id] = filename
            if verbose:
                print(f"  [{i:>3}/{total}] {item_id:<40} skip (exists)")
            continue

        if verbose:
            print(f"  [{i:>3}/{total}] {item_id:<40} ", end="", flush=True)

        image_url = fetch_url_fn(item_id)
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
        results[item_id] = filename
        if verbose:
            print(f"✓  ({len(image_data) // 1024} KB)")

        time.sleep(REQUEST_DELAY)

    return results


def scrape_card_images(
    card_ids: List[str],
    output_dir: str,
    *,
    skip_existing: bool = True,
    verbose: bool = True,
) -> Dict[str, str]:
    """Download card images for *card_ids* into *output_dir*."""
    return _scrape_images_generic(
        card_ids, output_dir, fetch_card_image_url,
        skip_existing=skip_existing, verbose=verbose,
    )


def scrape_relic_images(
    relic_ids: List[str],
    output_dir: str,
    *,
    skip_existing: bool = True,
    verbose: bool = True,
) -> Dict[str, str]:
    """Download relic images for *relic_ids* into *output_dir*."""
    return _scrape_images_generic(
        relic_ids, output_dir, fetch_relic_image_url,
        skip_existing=skip_existing, verbose=verbose,
    )


def scrape_potion_images(
    potion_ids: List[str],
    output_dir: str,
    *,
    skip_existing: bool = True,
    verbose: bool = True,
) -> Dict[str, str]:
    """Download potion images for *potion_ids* into *output_dir*."""
    return _scrape_images_generic(
        potion_ids, output_dir, fetch_potion_image_url,
        skip_existing=skip_existing, verbose=verbose,
    )


# ── Card/Relic/Potion ID discovery ────────────────────────────────────────────

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


def collect_relic_ids_from_runs(history_path: str) -> List[str]:
    """Return all unique relic IDs found in *.run files under *history_path*."""
    from .parser import load_run_files, _strip_prefix

    runs = load_run_files(history_path)
    relic_ids: set[str] = set()

    for run in runs:
        players = run.get("players", [])
        if not players:
            continue
        for r in players[0].get("relics") or []:
            rid = _strip_prefix(r.get("id", "")) if isinstance(r, dict) else _strip_prefix(str(r))
            if rid:
                relic_ids.add(rid)

        for act in run.get("map_point_history", []):
            for node in act:
                ps_list = node.get("player_stats", [])
                ps = ps_list[0] if ps_list else {}
                for entry in ps.get("relic_choices", []):
                    rid = _strip_prefix(entry.get("choice", ""))
                    if rid:
                        relic_ids.add(rid)
                for opt in ps.get("ancient_choice", []):
                    rid = opt.get("TextKey", "")
                    if rid:
                        relic_ids.add(rid)

    return sorted(relic_ids)


def collect_potion_ids_from_runs(history_path: str) -> List[str]:
    """Return all unique potion IDs found in *.run files under *history_path*."""
    from .parser import load_run_files, _strip_prefix

    runs = load_run_files(history_path)
    potion_ids: set[str] = set()

    for run in runs:
        players = run.get("players", [])
        if not players:
            continue
        for p in players[0].get("potions") or []:
            pid = _strip_prefix(p.get("id", "")) if isinstance(p, dict) else _strip_prefix(str(p))
            if pid:
                potion_ids.add(pid)

        for act in run.get("map_point_history", []):
            for node in act:
                ps_list = node.get("player_stats", [])
                ps = ps_list[0] if ps_list else {}
                for entry in ps.get("potion_choices", []):
                    pid = _strip_prefix(entry.get("choice", ""))
                    if pid:
                        potion_ids.add(pid)
                for pid_raw in ps.get("potion_used", []):
                    pid = _strip_prefix(pid_raw) if isinstance(pid_raw, str) else ""
                    if pid:
                        potion_ids.add(pid)

    return sorted(potion_ids)


# ── CLI entry point ───────────────────────────────────────────────────────────

def run_scrape(history_path: str, static_dir: str) -> None:
    """CLI entry: discover cards/relics/potions, download images, report results."""
    print(f"\n⚔  STS2 Image Scraper")
    print(f"   History path : {history_path}")
    print(f"   Static dir   : {static_dir}\n")

    card_output = os.path.join(static_dir, "card_images")
    relic_output = os.path.join(static_dir, "relic_images")
    potion_output = os.path.join(static_dir, "potion_images")

    print("Discovering card IDs from run files…")
    card_ids = collect_card_ids_from_runs(history_path)
    print(f"Found {len(card_ids)} unique card IDs.\n")
    print("Fetching card images from sts2.untapped.gg (with wiki fallback)…")
    dl_cards = scrape_card_images(card_ids, card_output, verbose=True)
    print(f"\nCards: downloaded {len(dl_cards)}/{len(card_ids)} images → {card_output}\n")

    print("Discovering relic IDs from run files…")
    relic_ids = collect_relic_ids_from_runs(history_path)
    print(f"Found {len(relic_ids)} unique relic IDs.\n")
    print("Fetching relic images from sts2.untapped.gg (with wiki fallback)…")
    dl_relics = scrape_relic_images(relic_ids, relic_output, verbose=True)
    print(f"\nRelics: downloaded {len(dl_relics)}/{len(relic_ids)} images → {relic_output}\n")

    print("Discovering potion IDs from run files…")
    potion_ids = collect_potion_ids_from_runs(history_path)
    print(f"Found {len(potion_ids)} unique potion IDs.\n")
    print("Fetching potion images from sts2.untapped.gg (with wiki fallback)…")
    dl_potions = scrape_potion_images(potion_ids, potion_output, verbose=True)
    print(f"\nPotions: downloaded {len(dl_potions)}/{len(potion_ids)} images → {potion_output}\n")
