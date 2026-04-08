# Copilot Instructions — STS2 Run Analyzer

## Project overview

A local Python + Flask web app for analyzing Slay the Spire 2 run history.
Run files (`.run`) are JSON documents dropped into `history/`; the app parses
them and serves a single-page dashboard at `http://localhost:5000`.

**Entry point:** `run.py`  
**Backend modules:** `analyzer/` (parser, stats, server, cli, scraper)  
**Frontend:** `templates/index.html` (single file, vanilla JS)  
**Static assets:** `static/` (Bootstrap 5, Chart.js, card/relic/potion images — all bundled locally, no CDN)

---

## Architecture

### Backend

- **`analyzer/parser.py`** — loads `.run` files and exposes low-level extraction
  helpers (`iter_nodes`, `extract_card_choices`, `extract_relic_events`, etc.).
  All data is plain `Dict[str, Any]`; no typed model classes.
- **`analyzer/stats.py`** — pure functions that take lists of run dicts and
  return computed stats. No I/O, no Flask imports.
- **`analyzer/server.py`** — Flask app factory (`create_app`). Thin: reads
  query params, calls `filter_runs`, delegates to `stats.*`, returns `jsonify`.
- **`analyzer/cli.py`** — diagnostic summary printed to stdout.
- **`analyzer/scraper.py`** — optional image scraper; run with `--scrape-images`.

### Frontend

- Single-page HTML (`templates/index.html`), no JS framework.
- Tab-based navigation; each tab fetches its own `/api/` endpoint lazily on
  first activation.
- All filters (character, ascension, ancient, exclude_multiplayer,
  exclude_abandoned, min_samples) are always visible and sent as query params
  on every fetch.
- Tables are sorted client-side by clicking column headers.

---

## Python conventions

- `from __future__ import annotations` at the top of every module.
- Type hints with `typing` imports (`List`, `Dict`, `Optional`, `Set`, `Any`).
- Module docstring format: `"""module.py — short description."""`
- Section separators: `# ── Section Name ───────────────────────────────`
- Private helpers prefixed with `_` (e.g. `_strip_prefix`, `_bool`).
- `snake_case` for everything (variables, functions, modules).
- `defaultdict` from `collections` for aggregation; avoid manual `setdefault` patterns.
- Docstrings on all public functions; describe parameters inline or with a
  short Parameters block when there are multiple.

---

## Data design rules

- **Never parse `.run` files twice** — `load_run_files` loads them once; all
  other code receives the list of dicts.
- **ID normalisation** — all STS2 IDs carry a type prefix (`CARD.BASH`,
  `RELIC.BURNING_BLOOD`). Always strip it with `_strip_prefix()` before
  storing or comparing.
- **`NONE.NONE` sentinel** — use `_is_none()` to test for null-equivalent IDs.
- **Ancient nodes** — use `ancient_choice` (not `relic_choices`) to avoid
  double-counting. This is handled in `extract_relic_events`; don't bypass it.
- **Relic classification** — `CHOICE_SOURCES = {"shop", "ancient"}` expose
  pick rate; everything else is `FORCED_SOURCES` (win rate only).
- **Stats are per-run, not per-offering** — use `Set[run_id]` for
  offered/picked/win tracking so a card offered twice in one run counts once.
- **Seed deduplication** — always call `filter_runs` (which deduplicates by
  seed) rather than filtering runs manually.
- **`_filename`** — injected as a metadata key by `load_run_files`; used for
  the run-detail endpoint. Don't remove or rename it.

---

## Flask / API conventions

- All API routes are prefixed `/api/`.
- Filters come from `request.args` via the `_filters()` helper in `server.py`;
  add new filter params there.
- Boolean query params are parsed by `_bool()` (handles `"false"`, `"0"`,
  `"no"`, `"off"`).
- `_load()` reloads run files on every request (stateless by design — the
  history folder may change between requests).
- Return data with `jsonify`; don't add Flask-specific logic to `stats.py`.

---

## Frontend conventions

- Dark STS-themed palette via CSS custom properties on `:root`:
  `--sts-bg`, `--sts-surface`, `--sts-border`, `--sts-gold`, `--sts-red`,
  `--sts-green`, `--sts-blue`, `--sts-purple`, `--sts-text`, `--sts-muted`.
- Use Bootstrap 5 utility classes and components; avoid inline styles.
- Badge classes follow the pattern `.badge-<source>` (e.g. `.badge-elite`,
  `.badge-shop`, `.badge-boss`, `.badge-win`, `.badge-loss`).
- All static dependencies (Bootstrap CSS, Chart.js) are bundled in `static/`;
  never add CDN links — the app must work offline.
- Keep all JS in `templates/index.html`; don't introduce a build step or JS
  framework.

---

## Key behaviours to preserve

- Multiplayer filter: `players.length > 1` runs are excluded when
  `exclude_multiplayer=true` (default on).
- Abandoned filter: runs abandoned on floor 1 (≤ 1 total node visited) are
  excluded when `exclude_abandoned=true` (default on). Later-abandoned runs
  are kept — they contain useful data.
- Ascension filter: exact match on `run["ascension"]`.
- Ancient filter: keeps only runs where the named ancient was encountered.
- Win/pick rates are `null` (not 0) when the denominator is 0, so the
  frontend can distinguish "never picked" from "picked but always lost".

---

## Dependency policy

- **Runtime:** only `Flask>=3.0.0` (see `requirements.txt`). Don't add new
  PyPI dependencies without a compelling reason.
- **Frontend:** no new JS libraries. Chart.js and Bootstrap are already bundled.
- Python 3.8+ compatibility required.
