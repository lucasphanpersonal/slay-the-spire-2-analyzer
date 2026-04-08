# Contributing to STS2 Run Analyzer

Thanks for your interest in contributing!  
This document describes how to get the project running locally, how the code is organized, and the conventions to follow when sending a pull request.

---

## Table of contents

1. [Prerequisites](#1-prerequisites)
2. [Local setup](#2-local-setup)
3. [Architecture overview](#3-architecture-overview)
4. [Module reference](#4-module-reference)
5. [Adding support for new .run schema fields](#5-adding-support-for-new-run-schema-fields)
6. [Adding a new stats tab](#6-adding-a-new-stats-tab)
7. [Code style & conventions](#7-code-style--conventions)
8. [Submitting a pull request](#8-submitting-a-pull-request)

---

## 1. Prerequisites

- Python 3.8+
- A handful of `.run` files to test with (see **README → Adding your run files**)

---

## 2. Local setup

```bash
# Clone the repo
git clone https://github.com/lucasphanpersonal/slay-the-spire-2-analyzer.git
cd slay-the-spire-2-analyzer

# Install dependencies (Flask only)
pip install -r requirements.txt

# Drop some .run files into history/ and start the dev server
python run.py
# → open http://localhost:5000
```

For a quick sanity-check without the browser:

```bash
python run.py --diagnostic
```

---

## 3. Architecture overview

```
run.py                  ← CLI entry point (argparse)
│
├─ analyzer/
│   ├─ __init__.py      ← package docstring / architecture notes
│   ├─ parser.py        ← loads & normalises raw .run JSON
│   ├─ stats.py         ← aggregates parsed runs into statistics
│   ├─ server.py        ← Flask REST API (consumed by the frontend)
│   ├─ cli.py           ← --diagnostic command implementation
│   └─ scraper.py       ← optional card-art image downloader
│
├─ templates/
│   └─ index.html       ← single-page dashboard (Bootstrap 5 + Chart.js)
│
└─ static/
    ├─ bootstrap.min.css
    ├─ chart.umd.min.js
    └─ card_images/     ← cached card art PNGs (populated by --scrape-images)
```

### Data flow

```
.run files on disk
       │
       ▼
parser.load_run_files()        → List[dict]   (raw run dicts)
       │
       ▼
stats.filter_runs()            → List[dict]   (filtered + deduplicated)
       │
       ▼
stats.compute_*()              → dict / List[dict]   (statistics)
       │
       ▼
server (Flask routes)          → JSON responses
       │
       ▼
templates/index.html           ← fetches /api/* endpoints, renders charts/tables
```

The **parser** layer is the only code that touches raw `.run` JSON. All schema quirks and field-name variations are handled there. Everything above it consumes normalised dicts.

---

## 4. Module reference

### `analyzer/parser.py`

| Symbol | Purpose |
|--------|---------|
| `load_run_files(path)` | Recursively load `*.run` files; returns a list of dicts |
| `iter_nodes(run)` | Yield `(node_type, player_stats, node)` for every map node |
| `extract_card_choices(run)` | List of `{offered, picked}` card-choice events |
| `extract_relic_events(run)` | List of relic acquisition events with source classification |
| `extract_encounters(run)` | List of combat nodes with damage and turn data |
| `extract_rest_sites(run)` | List of rest-site events with choice and HP healed |
| `extract_ancients(run)` | List of ancient-visit events with relic options |
| `run_total_damage(run)` | Sum of damage taken across all nodes |
| `run_final_hp/max_hp/gold` | Value at the last node visited |
| `run_deck_size(run)` | Final deck size from `players[0].deck` |
| `run_deck(run)` | Final deck with upgrade and enchantment info |
| `run_killed_by(run)` | Cleaned killed-by string, or `None` for wins |
| `CHOICE_SOURCES` | Frozenset of relic sources where pick rate is meaningful |
| `FORCED_SOURCES` | Frozenset of relic sources where only win rate is tracked |

### `analyzer/stats.py`

| Symbol | Purpose |
|--------|---------|
| `filter_runs(runs, **kwargs)` | Apply character/ascension/ancient filters + seed dedup |
| `get_characters/ascensions/ancients(runs)` | Sorted unique values for filter dropdowns |
| `compute_overview(runs)` | Win rate, avg damage, deck size, acts, deaths-by |
| `compute_cards(runs, min_offered)` | Per-card pick rate, win rate, upgrade stats |
| `compute_relics(runs)` | Choice vs forced relic statistics |
| `compute_encounters(runs)` | Per-encounter damage, turns, death rate |
| `compute_rest_sites(runs)` | Rest-site choice frequency and win rate |
| `compute_ancients(runs)` | Per-ancient stats with per-relic breakdowns |
| `compute_runs_list(runs)` | One summary row per run (Runs tab) |
| `compute_run_detail(run)` | Full detail for a single run (modal) |
| `compute_diagnostic(all_runs)` | Raw counts + schema anomaly list (no filtering) |

### `analyzer/server.py`

Thin Flask layer.  Each route calls `load_run_files → filter_runs → compute_*` and returns JSON.  All stat routes accept the same query parameters (`character`, `ascension`, `ancient`, `exclude_multiplayer`, `exclude_abandoned`).  See the `create_app` docstring for the full endpoint list.

### `analyzer/scraper.py`

Optional utility — not part of the normal dashboard flow.  Run with:

```bash
python run.py --scrape-images
```

This discovers all card IDs from your run files and attempts to download matching card-art PNGs from the STS2 wiki into `static/card_images/`.

---

## 5. Adding support for new .run schema fields

1. Open `analyzer/parser.py`.
2. Add a new helper function (or extend an existing `extract_*` function) to read the new field.
3. Keep all `run.get(...)` calls and fallback handling inside `parser.py`.
4. If the new data should appear in the dashboard, add a corresponding `compute_*` function in `stats.py` and a new route in `server.py`.

---

## 6. Adding a new stats tab

1. **Backend** — add a `compute_<tab>(runs)` function in `stats.py` and a matching route in `server.py` (e.g. `GET /api/<tab>`).
2. **Frontend** — in `templates/index.html`:
   - Add a nav `<li>` button for the new tab.
   - Add a `<div id="tab-<tab>">` panel.
   - Add a `fetchTab<Tab>()` JS function that calls your new endpoint and renders the data.
   - Hook it up in the `showTab()` switch statement.

---

## 7. Code style & conventions

- **Python 3.8+** compatible syntax throughout.
- Type annotations on all public functions (use `from __future__ import annotations`).
- Docstrings on all public functions — use Google / NumPy style (parameters listed under a `Parameters` heading for multi-param functions).
- `Dict`, `List`, `Optional` from `typing` (not the built-in generics) for 3.8 compatibility.
- No external dependencies beyond Flask. Use the standard library for everything else.
- The frontend is intentionally dependency-free at runtime (Bootstrap and Chart.js are bundled locally in `static/`).

---

## 8. Submitting a pull request

1. Fork the repository and create a feature branch.
2. Make your changes and test them manually with real `.run` files.
3. Run a quick diagnostic to make sure nothing is broken:
   ```bash
   python run.py --diagnostic
   ```
4. Open a pull request against `main` with a clear description of what changed and why.

If you're adding support for a new schema variant, please include a small anonymised sample `.run` file (or a snippet) in the PR description so the change can be verified.
