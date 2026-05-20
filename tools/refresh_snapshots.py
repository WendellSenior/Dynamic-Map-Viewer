"""Rebuild every campaign's snapshots.json from the PNGs in its maps/ folder.
Preserves existing date/label edits — entries whose image filename already
appears in snapshots.json are kept verbatim. New PNGs get a date inferred
from the filename (Y_M_D, Y-M-D, or bare year) and a year-only label."""

import json
import re
from pathlib import Path


DATE_RE = re.compile(r"(?<!\d)(\d{3,4})[_\-](\d{1,2})[_\-](\d{1,2})(?!\d)")
YEAR_RE = re.compile(r"(?<!\d)(\d{3,4})(?!\d)")

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def derive_date(stem):
    m = DATE_RE.search(stem)
    if m:
        y, mo, d = m.groups()
        if 1 <= int(mo) <= 12 and 1 <= int(d) <= 31:
            return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    m = YEAR_RE.search(stem)
    if m:
        y = int(m.group(1))
        if 1000 <= y <= 2999:
            return f"{y:04d}-01-01"
    return None


def refresh_one(snapshots_path, maps_dir):
    existing = (
        json.loads(snapshots_path.read_text(encoding="utf-8"))
        if snapshots_path.exists()
        else {}
    )
    config = existing.get("config", {})
    by_image = {s["image"]: s for s in existing.get("snapshots", [])}

    new_snapshots = []
    skipped = []
    for path in sorted(maps_dir.iterdir()):
        if path.suffix.lower() not in IMAGE_EXTS:
            continue
        rel = f"data/maps/{path.name}"
        if rel in by_image:
            new_snapshots.append(by_image[rel])
            continue
        date = derive_date(path.stem)
        if date is None:
            skipped.append(path.name)
            continue
        new_snapshots.append({
            "date": date,
            "image": rel,
            "label": date.split("-")[0],
        })
    new_snapshots.sort(key=lambda s: s["date"])

    out = {"config": config, "snapshots": new_snapshots}
    snapshots_path.write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return new_snapshots, skipped


def main():
    repo_root = Path(__file__).resolve().parent.parent
    touched = 0
    for snap_path in sorted(repo_root.glob("*/data/snapshots.json")):
        maps_dir = snap_path.parent / "maps"
        if not maps_dir.is_dir():
            continue
        snaps, skipped = refresh_one(snap_path, maps_dir)
        campaign = snap_path.parent.parent.name
        msg = f"{campaign}: {len(snaps)} snapshot(s)"
        if skipped:
            msg += f"  (skipped, no date in filename: {', '.join(skipped)})"
        print(msg)
        touched += 1
    if touched == 0:
        print("No campaign folders found (expected */data/snapshots.json)")


if __name__ == "__main__":
    main()
