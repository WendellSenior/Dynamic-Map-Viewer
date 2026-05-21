"""Run preprocess.py once per campaign that has a Discord export.
Discovers campaigns by scanning for <campaign>/data/discord/*.html.
Auto-detects each campaign's game from its data/reference/<game>/ folder."""

import json
import subprocess
import sys
from pathlib import Path


HTML_EXTS = {".html", ".json"}


def _sync_owned_folders(repo_root):
    """Returns the set of campaign folder names whose events.json is managed
    by the GH-Actions Discord sync — preprocess would overwrite their live
    state with stale local data."""
    out = set()
    manifest_path = repo_root / "campaigns.json"
    if not manifest_path.exists():
        return out
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return out
    for entry in manifest.get("campaigns", []):
        if (entry.get("discord_sync") or {}).get("enabled"):
            if entry.get("folder"):
                out.add(entry["folder"])
    return out


def main():
    repo_root = Path(__file__).resolve().parent.parent
    preprocess = repo_root / "tools" / "preprocess.py"
    sync_owned = _sync_owned_folders(repo_root)
    ran = 0
    for snap_path in sorted(repo_root.glob("*/data/snapshots.json")):
        campaign = snap_path.parent.parent
        if campaign.name in sync_owned:
            print(f"  {campaign.name}: skipped (Discord sync owns events.json)")
            continue
        discord_dir = campaign / "data" / "discord"
        if not discord_dir.is_dir():
            continue
        # Process every export in the campaign's discord/ folder, not just the latest.
        # preprocess.py handles directories: it expands to all .html / .json children.
        has_inputs = any(
            f.suffix.lower() in HTML_EXTS and not f.name.startswith(".")
            for f in discord_dir.iterdir()
        )
        if not has_inputs:
            continue

        # Game = first subfolder of data/reference/, by convention. After the
        # shared-reference refactor, that per-campaign subfolder only contains
        # aliases.json (the interactive lookup cache); the game data itself
        # lives at <repo-root>/assets/reference/<game>/.
        per_campaign_ref = campaign / "data" / "reference"
        game = None
        if per_campaign_ref.is_dir():
            for r in sorted(per_campaign_ref.iterdir()):
                if r.is_dir():
                    game = r.name
                    break
        if not game:
            print(f"  {campaign.name}: skipped (no data/reference/<game>/)")
            continue

        shared_ref = repo_root / "assets" / "reference" / game
        if not shared_ref.is_dir():
            print(f"  {campaign.name}: skipped (no shared reference at {shared_ref})")
            continue

        cmd = [
            sys.executable, str(preprocess), str(discord_dir),
            "--out",          str(campaign / "data" / "events.json"),
            "--tags",         str(shared_ref / "tags.json"),
            "--raw-tags",     str(shared_ref / "00_countries.txt"),
            "--aliases",      str(per_campaign_ref / game / "aliases.json"),
            "--untagged-log", str(campaign / "data" / "untagged.log"),
            "--non-interactive",
        ]
        print(f"  {campaign.name}: preprocessing all exports in {discord_dir.name}/")
        subprocess.run(cmd, check=False)
        ran += 1

    if ran == 0:
        print("preprocess_all: no campaigns with Discord exports found")


if __name__ == "__main__":
    main()
