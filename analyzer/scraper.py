"""analyzer/scraper.py — Scrape and cache card art images and metadata from sts2.untapped.gg (with wiki fallback)."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

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


# ── untapped.gg card page data extraction ────────────────────────────────────

# Known field name sets for each metadata attribute.
# Keys are normalized (lowercase, no separators).
_METADATA_CANDIDATES: Dict[str, frozenset] = {
    "character":          frozenset({"character", "class", "characterclass", "playerclass", "char"}),
    "card_type":          frozenset({"type", "cardtype", "card_type"}),
    "cost":               frozenset({"cost", "energy", "energycost", "manacost"}),
    "rarity":             frozenset({"rarity", "tier", "cardtier"}),
    "description":        frozenset({"description", "desc", "text", "basedescription", "cardtext", "body"}),
    "description_upgraded": frozenset({
        "upgradeddescription", "upgradedtext", "upgradeddesc",
        "upgradecardtext", "upgradetext", "upgradedbody",
    }),
}


def _walk_for_card_fields(obj: Any, result: Dict[str, Any], depth: int = 0) -> None:
    """Recursively walk a JSON object tree and fill in *result* metadata fields."""
    if depth > 20:
        return
    if isinstance(obj, dict):
        for key, value in obj.items():
            key_norm = re.sub(r"[_\-\s]", "", key.lower())
            for field, candidates in _METADATA_CANDIDATES.items():
                if result[field] is None and key_norm in candidates:
                    if field == "cost" and isinstance(value, (int, float)):
                        result[field] = int(value)
                    elif field != "cost" and isinstance(value, str) and value.strip():
                        result[field] = value.strip()
            if isinstance(value, (dict, list)):
                _walk_for_card_fields(value, result, depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            _walk_for_card_fields(item, result, depth + 1)


def _extract_from_next_data(
    next_data: Any,
    result: Dict[str, Any],
    slug: str,
) -> None:
    """Pull image URLs and metadata from a parsed ``__NEXT_DATA__`` blob."""
    json_str = json.dumps(next_data)

    # Collect all image-like URLs that contain the card slug
    img_urls = re.findall(
        r'https?://[^\s"\'<>]+(?:\.png|\.jpg|\.jpeg|\.webp)',
        json_str,
        re.IGNORECASE,
    )
    slug_imgs = [u for u in img_urls if slug in u.lower()]

    # Normal image fallback (og:image may already be set)
    if not result["image_url"] and slug_imgs:
        result["image_url"] = slug_imgs[0]

    # Upgraded image: prefer URLs hinting at upgrade, then take a distinct second URL
    if not result["upgraded_image_url"]:
        upgrade_hints = [u for u in slug_imgs if any(x in u.lower() for x in ("upgrade", "upgraded", "+1"))]
        if upgrade_hints:
            result["upgraded_image_url"] = upgrade_hints[0]
        elif len(slug_imgs) >= 2 and slug_imgs[1] != slug_imgs[0]:
            result["upgraded_image_url"] = slug_imgs[1]

    # Metadata from JSON tree
    _walk_for_card_fields(next_data, result)


def _fetch_card_page_data(card_id: str) -> Dict[str, Any]:
    """Fetch the untapped.gg card page for *card_id* and extract all available data.

    Returns a dict with:
    ``image_url``, ``upgraded_image_url``, ``character``, ``card_type``,
    ``cost``, ``rarity``, ``description``, ``description_upgraded``.

    Missing fields are ``None``.
    """
    result: Dict[str, Any] = {
        "image_url": None,
        "upgraded_image_url": None,
        "character": None,
        "card_type": None,
        "cost": None,
        "rarity": None,
        "description": None,
        "description_upgraded": None,
    }

    slug = card_id_to_slug(card_id)
    page_url = f"{UNTAPPED_BASE}{UNTAPPED_CARDS_PATH}/{slug}"
    html_bytes = _get(page_url)
    if not html_bytes:
        return result

    try:
        html = html_bytes.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return result

    # 1. Open Graph og:image (most reliable — server-rendered for SEO)
    og_match = re.search(
        r'<meta[^>]+(?:'
        r'property=["\']og:image["\'][^>]+content=["\'](https?://[^"\']+)["\']'
        r'|content=["\'](https?://[^"\']+)["\'][^>]+property=["\']og:image["\']'
        r')',
        html,
        re.IGNORECASE,
    )
    if og_match:
        result["image_url"] = og_match.group(1) or og_match.group(2)

    # 2. Next.js __NEXT_DATA__ JSON blob — images + metadata
    next_match = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html,
        re.DOTALL | re.IGNORECASE,
    )
    if next_match:
        try:
            next_data = json.loads(next_match.group(1))
            _extract_from_next_data(next_data, result, slug)
        except Exception:  # noqa: BLE001
            pass

    # 3. Fallback: any <img src="..."> whose URL contains the card slug
    if not result["image_url"]:
        img_matches = re.findall(
            r'<img[^>]+src=["\'](https?://[^"\']+)["\']',
            html,
            re.IGNORECASE,
        )
        for src in img_matches:
            if slug in src.lower():
                result["image_url"] = src
                break

    return result


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
    page_data = _fetch_card_page_data(card_id)
    if page_data["image_url"]:
        return page_data["image_url"]
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


def scrape_card_data(
    card_ids: List[str],
    output_dir: str,
    *,
    skip_existing: bool = True,
    verbose: bool = True,
) -> Dict[str, Dict[str, Any]]:
    """Download card images (normal + upgraded) and scrape metadata for *card_ids*.

    Saves into *output_dir*:

    * ``{card_id_lower}.png``          — normal card art
    * ``{card_id_lower}_upgraded.png`` — upgraded card art (when available)
    * ``card_data.json``               — metadata for all scraped cards

    Parameters
    ----------
    card_ids:
        List of normalized card IDs.
    output_dir:
        Directory to save images and ``card_data.json`` into.
    skip_existing:
        When True, skip cards where both images and metadata are already cached.
    verbose:
        Print progress.

    Returns
    -------
    The full card data dict keyed by card ID.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    data_file = out / "card_data.json"

    # Load existing card_data.json to allow incremental runs
    card_data: Dict[str, Dict[str, Any]] = {}
    if data_file.exists():
        try:
            with open(data_file, encoding="utf-8") as f:
                card_data = json.load(f)
        except Exception:  # noqa: BLE001
            card_data = {}

    total = len(card_ids)

    for i, card_id in enumerate(sorted(card_ids), 1):
        normal_filename = card_id_to_filename(card_id)
        upgraded_filename = card_id.lower() + "_upgraded.png"
        normal_dest = out / normal_filename
        upgraded_dest = out / upgraded_filename

        normal_exists = normal_dest.exists()
        upgraded_exists = upgraded_dest.exists()
        has_meta = card_id in card_data

        if skip_existing and normal_exists and upgraded_exists and has_meta:
            if verbose:
                print(f"  [{i:>3}/{total}] {card_id:<40} skip (all cached)")
            continue

        if verbose:
            print(f"  [{i:>3}/{total}] {card_id:<40} ", end="", flush=True)

        # ── Fetch page data from untapped.gg ──────────────────────────────
        page_data = _fetch_card_page_data(card_id)
        time.sleep(REQUEST_DELAY)

        # Fallback to wiki for normal image if untapped.gg didn't return one
        if not page_data["image_url"]:
            for file_title in _candidate_file_titles(card_id):
                wiki_url = _resolve_image_url_via_api(file_title)
                if wiki_url:
                    page_data["image_url"] = wiki_url
                    break
                time.sleep(REQUEST_DELAY)

        downloaded: List[str] = []

        # ── Download normal image ─────────────────────────────────────────
        if page_data["image_url"] and not (skip_existing and normal_exists):
            img_bytes = _get(page_data["image_url"])
            if img_bytes:
                normal_dest.write_bytes(img_bytes)
                downloaded.append("art")
                time.sleep(REQUEST_DELAY)

        # ── Download upgraded image ───────────────────────────────────────
        if page_data["upgraded_image_url"] and not (skip_existing and upgraded_exists):
            img_bytes = _get(page_data["upgraded_image_url"])
            if img_bytes:
                upgraded_dest.write_bytes(img_bytes)
                downloaded.append("art+")
                time.sleep(REQUEST_DELAY)

        # ── Store metadata ────────────────────────────────────────────────
        card_data[card_id] = {
            "character":            page_data["character"],
            "card_type":            page_data["card_type"],
            "cost":                 page_data["cost"],
            "rarity":               page_data["rarity"],
            "description":          page_data["description"],
            "description_upgraded": page_data["description_upgraded"],
            "has_image":            normal_dest.exists(),
            "has_upgraded_image":   upgraded_dest.exists(),
        }

        if verbose:
            if downloaded:
                print(f"✓  ({', '.join(downloaded)})")
            else:
                print("metadata only")

    # Persist card_data.json after every run so partial results are saved
    with open(data_file, "w", encoding="utf-8") as f:
        json.dump(card_data, f, indent=2, ensure_ascii=False)
    if verbose:
        print(f"\nSaved card data → {data_file}")

    return card_data


# ── Card ID discovery ─────────────────────────────────────────────────────────

def _slug_to_card_id(slug: str) -> str:
    """Convert an untapped.gg card slug back to a card ID.

    Examples:
        bash          → BASH
        all-for-one   → ALL_FOR_ONE
        setup-strike  → SETUP_STRIKE
    """
    return slug.upper().replace("-", "_")


def fetch_all_card_ids_from_untapped() -> List[str]:
    """Fetch the full list of card IDs from sts2.untapped.gg/en/cards.

    Parses card slug links (``/en/cards/<slug>``) from the page HTML and the
    embedded ``__NEXT_DATA__`` JSON blob, then converts each slug to a card ID.

    Returns a sorted list of unique card IDs, or an empty list on failure.
    """
    url = f"{UNTAPPED_BASE}{UNTAPPED_CARDS_PATH}"
    html_bytes = _get(url)
    if not html_bytes:
        return []

    try:
        html = html_bytes.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return []

    slugs: Set[str] = set()

    # 1. Extract slugs from <a href="/en/cards/{slug}"> links in the HTML.
    #    The slug portion must contain at least one letter to avoid bare
    #    pagination or locale segments.
    for slug in re.findall(
        r'href=["\'](?:/[a-z]{2})?/cards/([A-Za-z][A-Za-z0-9\-]*)["\']',
        html,
    ):
        slugs.add(slug.lower())

    # 2. Also mine the __NEXT_DATA__ blob for any card slug paths we may have
    #    missed (e.g. in pre-fetched route props or server-side JSON payloads).
    next_match = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html,
        re.DOTALL | re.IGNORECASE,
    )
    if next_match:
        try:
            next_json_str = next_match.group(1)
            # Pull every "/en/cards/<slug>" occurrence from the raw JSON text.
            for slug in re.findall(
                r'/cards/([A-Za-z][A-Za-z0-9\-]*)',
                next_json_str,
            ):
                slugs.add(slug.lower())
        except Exception:  # noqa: BLE001
            pass

    return sorted(_slug_to_card_id(s) for s in slugs)


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
    """CLI entry: discover cards, download images + metadata, report results."""
    print(f"\n⚔  STS2 Card Image Scraper")
    print(f"   History path : {history_path}")
    print(f"   Output dir   : {output_dir}\n")

    # Primary: fetch the full card catalogue directly from untapped.gg
    print("Fetching full card list from sts2.untapped.gg/en/cards…")
    site_ids = fetch_all_card_ids_from_untapped()
    if site_ids:
        print(f"Found {len(site_ids)} card IDs from site.")
    else:
        print("  ⚠  Could not fetch card list from site; falling back to run history.")

    # Secondary: cards seen in local run history (union ensures none are missed)
    print("Discovering card IDs from run files…")
    run_ids = collect_card_ids_from_runs(history_path)
    print(f"Found {len(run_ids)} unique card IDs from run history.")

    card_ids = sorted(set(site_ids) | set(run_ids))
    print(f"Total unique card IDs to scrape: {len(card_ids)}\n")

    print("Fetching card data from sts2.untapped.gg (images + metadata)…")
    card_data = scrape_card_data(card_ids, output_dir, verbose=True)

    has_image = sum(1 for v in card_data.values() if v.get("has_image"))
    has_upgraded = sum(1 for v in card_data.values() if v.get("has_upgraded_image"))
    has_meta = sum(1 for v in card_data.values() if v.get("character") or v.get("description"))
    total = len(card_ids)
    print(
        f"\nDone.  {has_image}/{total} normal images, "
        f"{has_upgraded}/{total} upgraded images, "
        f"{has_meta}/{total} with metadata  →  {output_dir}"
    )
