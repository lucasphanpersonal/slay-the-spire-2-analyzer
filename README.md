# ⚔ Slay the Spire 2 Run Analyzer

A local CLI + web dashboard for analyzing your Slay the Spire 2 run history.

![STS2 Run Analyzer Overview](https://github.com/user-attachments/assets/d84e0137-3f33-467b-89e7-f921862728bc)

## Features

- **Overview** — win rate, avg damage taken, avg deck size, avg run time, acts-reached distribution, deaths-by breakdown
- **Cards** — pick rate (times picked / times offered), win rate on runs where picked, sortable, filterable by name, configurable minimum sample size
- **Relics** — choice relics (shop/ancient) with pick rate, forced relics (treasure/elite/event) with win-rate-with-relic, source badges
- **Encounters** — avg damage taken per encounter, avg turns, type badge (monster/elite/boss), sortable
- **Rest Sites** — frequency of each choice (Smith/Rest/Toke/etc.), win rate, avg HP healed for Rest
- **Runs** — individual run list with character, win/loss, ascension, HP bar, gold, deck size, relics, damage taken, killed by

### Filters (always visible)
- Character selector
- Exclude multiplayer toggle (on by default)
- Exclude abandoned (floor 1) toggle (on by default)
- Min samples (for Cards tab)

### Data handling
- Solo runs only (`players.length == 1`) when multiplayer filter is active
- Abandoned runs on floor 1 filtered out (later-abandoned runs are kept)
- Ancient nodes: uses `ancient_choice` and skips `relic_choices` to avoid double-counting
- Relic source classification: shop/ancient = choice (pick rate), everything else = forced (win rate only)
- Win/pick rates are tracked per-run, not per-offering
- Seed deduplication

## Setup

**Requirements:** Python 3.8+

```bash
pip install -r requirements.txt
```

## Usage

### Start the web dashboard

```bash
python run.py
```

Then open **http://localhost:5000** in your browser.

### Options

```
python run.py --history /path/to/run/files   # custom history folder (default: ./history)
python run.py --port 8080                     # custom port (default: 5000)
python run.py --diagnostic                    # print diagnostic summary and exit
```

### Run a diagnostic summary

```bash
python run.py --diagnostic
```

Output example:
```
⚔  STS2 Run Analyzer — Diagnostic Summary
   History path: ./history

  Files loaded      : 20
  Solo runs         : 19
  Multiplayer runs  : 1
  Wins              : 11
  Losses            : 8
  Abandoned (fl. 1) : 1
  Duplicate seeds   : 1

  Characters present:
    IRONCLAD                       9 run(s)
    THE_DEFECT                     3 run(s)
    THE_SILENT                     5 run(s)
    THE_WATCHER                    3 run(s)

  ✓  No schema anomalies detected.
```

## Adding your run files

Drop your `.run` files into the `history/` folder (subdirectories are supported).

Your run files are typically located at:
- **Windows:** `C:\Users\lucas\AppData\Roaming\SlayTheSpire2\steam\76561198000002844\profile1\saves\history`

## Expected `.run` file schema

The analyzer expects JSON files with the following top-level structure:

```json
{
  "players": [{ "character": "CHARACTER.IRONCLAD" }],
  "win": true,
  "ascension": 5,
  "seed": "ABC123",
  "run_time": 3600,
  "was_abandoned": false,
  "game_mode": "NORMAL",
  "killed_by_encounter": null,
  "killed_by_event": null,
  "map_point_history": [
    [
      {
        "map_point_type": "monster",
        "encounter_id": "Jaw Worm",
        "player_stats": [{
          "card_choices": [
            { "cards_offered": ["Strike", "Defend", "Bash"], "card_picked": "Bash" }
          ],
          "relic_choices": [],
          "ancient_choice": null,
          "rest_site_choices": [],
          "damage_taken": 12,
          "current_hp": 68,
          "max_hp": 80,
          "current_gold": 99,
          "hp_healed": 0,
          "upgraded_cards": [],
          "cards_removed": []
        }]
      }
    ]
  ]
}
```

For **ancient nodes**, `ancient_choice` should be a list of options:
```json
"ancient_choice": [
  { "relic": "Cursed Key",   "picked": false },
  { "relic": "Ectoplasm",    "picked": true  },
  { "relic": "Odd Mushroom", "picked": false }
]
```

> **Note:** The analyzer handles multiple schema variants (different field names, etc.). If your run files use a different format, open an issue.

## Tech stack

- **Backend:** Python + [Flask](https://flask.palletsprojects.com/)
- **Frontend:** Single-page HTML with Bootstrap 5 + Chart.js (bundled locally — works offline)
