"""analyzer/cli.py — CLI diagnostic summary."""

from __future__ import annotations

from .parser import load_run_files
from .stats import compute_diagnostic


def run_diagnostic(history_path: str) -> None:
    print(f"\n⚔  STS2 Run Analyzer — Diagnostic Summary")
    print(f"   History path: {history_path}\n")

    all_runs = load_run_files(history_path)

    if not all_runs:
        print("  ⚠  No .run files found.  Drop your run files into the history folder.\n")
        return

    d = compute_diagnostic(all_runs)

    print(f"  Files loaded      : {d['total_files']}")
    print(f"  Solo runs         : {d['solo_runs']}")
    print(f"  Multiplayer runs  : {d['multiplayer_runs']}")
    print(f"  Wins              : {d['wins']}")
    print(f"  Losses            : {d['losses']}")
    print(f"  Abandoned (fl. 1) : {d['abandoned_first_floor']}")
    print(f"  Duplicate seeds   : {d['duplicate_seeds']}\n")

    print(f"  Characters present:")
    for char, count in d["characters"].items():
        print(f"    {char:<30} {count} run(s)")

    if d["schema_anomalies"]:
        print(f"\n  ⚠  Schema anomalies ({len(d['schema_anomalies'])}):")
        for a in d["schema_anomalies"]:
            print(f"    • {a}")
    else:
        print(f"\n  ✓  No schema anomalies detected.")

    print()
