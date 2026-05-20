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
    lowres_dir = maps_dir / "lowres"

    new_snapshots = []
    skipped = []
    for path in sorted(maps_dir.iterdir()):
        if path.suffix.lower() not in IMAGE_EXTS:
            continue
        rel = f"data/maps/{path.name}"
        if rel in by_image:
            snap = dict(by_image[rel])
        else:
            date = derive_date(path.stem)
            if date is None:
                skipped.append(path.name)
                continue
            snap = {
                "date": date,
                "image": rel,
                "label": date.split("-")[0],
            }
        # Sync the lowres pointer with what's on disk — accept any image extension.
        lowres_match = None
        for ext in IMAGE_EXTS:
            cand = lowres_dir / (path.stem + ext)
            if cand.exists():
                lowres_match = cand
                break
        if lowres_match is not None:
            snap["image_lowres"] = f"data/maps/lowres/{lowres_match.name}"
        else:
            snap.pop("image_lowres", None)
        new_snapshots.append(snap)
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
