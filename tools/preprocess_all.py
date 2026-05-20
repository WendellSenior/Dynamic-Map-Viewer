"""Run preprocess.py once per campaign that has a Discord export.
Discovers campaigns by scanning for <campaign>/data/discord/*.html.
Auto-detects each campaign's game from its data/reference/<game>/ folder."""

import subprocess
import sys
from pathlib import Path


HTML_EXTS = {".html", ".json"}


def main():
    repo_root = Path(__file__).resolve().parent.parent
    preprocess = repo_root / "tools" / "preprocess.py"
    ran = 0
    for snap_path in sorted(repo_root.glob("*/data/snapshots.json")):
        campaign = snap_path.parent.parent
        discord_dir = campaign / "data" / "discord"
        if not discord_dir.is_dir():
            continue

        # Latest export alphabetically (DCE filenames sort by date).
        latest = None
        for f in sorted(discord_dir.iterdir()):
            if f.suffix.lower() in HTML_EXTS and not f.name.startswith("."):
                latest = f
        if not latest:
            continue

        # Game = first subfolder of data/reference/, by convention.
        ref_root = campaign / "data" / "reference"
        game = None
        if ref_root.is_dir():
            for r in sorted(ref_root.iterdir()):
                if r.is_dir():
                    game = r.name
                    break
        if not game:
            print(f"  {campaign.name}: skipped (no data/reference/<game>/)")
            continue

        ref = ref_root / game
        cmd = [
            sys.executable, str(preprocess), str(latest),
            "--out",          str(campaign / "data" / "events.json"),
            "--tags",         str(ref / "tags.json"),
            "--raw-tags",     str(ref / "00_countries.txt"),
            "--aliases",      str(ref / "aliases.json"),
            "--untagged-log", str(campaign / "data" / "untagged.log"),
            "--non-interactive",
        ]
        print(f"  {campaign.name}: preprocessing {latest.name}")
        subprocess.run(cmd, check=False)
        ran += 1

    if ran == 0:
        print("preprocess_all: no campaigns with Discord exports found")


if __name__ == "__main__":
    main()
