"""Slay the Spire 2 Run Analyzer — core package.

Package layout
--------------
analyzer/
    __init__.py   — this file; package-level exports and architecture notes
    parser.py     — low-level .run file loading and per-run data extraction
    stats.py      — aggregation functions that turn parsed runs into statistics
    server.py     — Flask app with REST API endpoints consumed by the dashboard
    cli.py        — CLI entry point for the ``--diagnostic`` mode
    scraper.py    — optional card-art image downloader (wiki scraper)

Typical data flow
-----------------
1. ``parser.load_run_files(path)``        → raw list of run dicts
2. ``stats.filter_runs(runs, **kwargs)``  → filtered / deduplicated run list
3. ``stats.compute_*(runs)``              → statistics dicts / lists
4. ``server`` routes serialise results to JSON for the frontend

The ``parser`` module is the only layer that touches raw run-file JSON.
All other modules consume the normalised dicts it produces, so schema
quirks only need to be handled once.

Public re-exports
-----------------
Nothing is re-exported at the package level; import directly from submodules:

    from analyzer.parser import load_run_files
    from analyzer.stats import filter_runs, compute_overview
"""
