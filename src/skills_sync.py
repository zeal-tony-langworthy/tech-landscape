#!/usr/bin/env python3
"""
Sync developer skills from PDFs in Google Drive into a Landscape2 data.yml.

Three steps, run in order:

  extract  Read each PDF in the Drive folder (in memory) and ask Claude which
           technical skills it lists. Results are cached per file in
           ./extracted/, so reruns only process new or changed PDFs.

  review   Aggregate all extractions, find skills that aren't in data.yml yet,
           and ask Claude to place each one in an existing category and
           subcategory. Writes new_skills.yml for you to review and edit.

  apply    Add the approved entries from new_skills.yml to data.yml,
           preserving the file's existing formatting.

Environment variables:
  ANTHROPIC_API_KEY             Claude API key (required for extract/review)
  GOOGLE_OAUTH_CLIENT_FILE      OAuth client JSON; signs in as you (extract)
  GOOGLE_SERVICE_ACCOUNT_FILE   Or: service account key JSON (extract)
  DRIVE_FOLDER_ID               ID of the Drive folder holding the PDFs (extract)
  CLAUDE_MODEL                  Optional, defaults to claude-sonnet-5
"""
from __future__ import annotations

import argparse
import helpers
import extract
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=Path("data.yml"), help="Landscape data file")
    parser.add_argument("--logos", type=Path, default=Path("logos"), help="Logos directory")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check-drive", help="Test Drive access and list the PDFs found")
    p.set_defaults(func=helpers.cmd_check_drive)

    p = sub.add_parser("extract", help="Extract skills from the Drive PDFs")
    p.add_argument("--force", action="store_true", help="Reprocess every PDF, ignoring the cache")
    p.set_defaults(func=extract.cmd_extract)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()