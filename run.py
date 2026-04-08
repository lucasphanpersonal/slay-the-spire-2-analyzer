#!/usr/bin/env python3
"""Slay the Spire 2 Run Analyzer — Entry Point.

Usage:
    python run.py                          # start web dashboard (default port 5000)
    python run.py --port 8080              # custom port
    python run.py --history /path/to/runs  # custom history folder
    python run.py --diagnostic             # print diagnostic summary and exit
    python run.py --scrape-images          # download card, relic, and potion art images and exit
"""

import argparse
import sys
import os


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Slay the Spire 2 Run Analyzer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--history",
        default="./history",
        help="Path to folder containing .run files (default: ./history)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=5000,
        help="Web server port (default: 5000)",
    )
    parser.add_argument(
        "--diagnostic",
        action="store_true",
        help="Print diagnostic summary and exit without starting the server",
    )
    parser.add_argument(
        "--scrape-images",
        action="store_true",
        help="Download card, relic, and potion art images into static/ subdirectories and exit",
    )
    args = parser.parse_args()

    history_path = os.path.abspath(args.history)

    if args.diagnostic:
        from analyzer.cli import run_diagnostic
        run_diagnostic(history_path)
    elif args.scrape_images:
        root = os.path.dirname(os.path.abspath(__file__))
        static_dir = os.path.join(root, "static")
        from analyzer.scraper import run_scrape
        run_scrape(history_path, static_dir)
    else:
        from analyzer.server import create_app
        app = create_app(history_path)
        url = f"http://localhost:{args.port}"
        print(f"  ⚔  STS2 Run Analyzer")
        print(f"  ➜  Dashboard:    {url}")
        print(f"  ➜  History path: {history_path}")
        print(f"  ➜  Press Ctrl+C to stop\n")
        app.run(host="0.0.0.0", port=args.port, debug=False)


if __name__ == "__main__":
    main()
