"""Rebuild every campaign's snapshots.json from the PNGs in its maps/ folder.
Preserves existing date/label edits — entries whose image filename already
appears in snapshots.json are kept verbatim. New PNGs get a date inferred
from the filename (Y_M_D, Y-M-D, or bare year) and a year-only label.

Also auto-generates half-scale lowres companions in maps/lowres/ for every
source map that doesn't already have one. Pillow required for that step;
skipped silently with a hint if it isn't installed. Delete a lowres file to
force regeneration. The "Half quality" toggle in the viewer points at these."""

import json
import re
from pathlib import Path


DATE_RE = re.compile(r"(?<!\d)(\d{3,4})[_\-](\d{1,2})[_\-](\d{1,2})(?!\d)")
YEAR_RE = re.compile(r"(?<!\d)(\d{3,4})(?!\d)")

# Tuple, not set — the snapshot lookup walks these in order to pick the lowres
# companion when multiple formats exist for the same stem. Webp first because
# that's what generate_missing_lowres writes; the rest cover hand-placed files.
IMAGE_EXTS = (".webp", ".png", ".jpg", ".jpeg")

# How much to shrink full-res maps when generating lowres companions. 0.5 =
# half on each axis = quarter pixel count = the "Half quality" choice in the
# viewer's resolution toggle.
LOWRES_SCALE = 0.5

# Lowres files are saved as WebP regardless of source format. Reasoning:
# Paradox map exports tend to be 8-bit indexed PNGs (~2 MB at 16384x8192
# thanks to palette compression). LANCZOS-resampling them produces anti-
# aliased pixels that don't fit the palette, so a 24-bit PNG save balloons
# OVER the source. WebP at quality 80 yields ~300-800 KB per map for the
# same content — actually fulfilling the "lowres = faster page load" promise.
LOWRES_EXT     = ".webp"
LOWRES_QUALITY = 80


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


def generate_missing_lowres(maps_dir):
    """For every full-res source image in `maps_dir`, ensure a half-scale
    companion exists in `maps_dir/lowres/`. Returns (generated, total) — the
    number of files newly written and the total source-image count examined.

    Behaviour:
      - Pillow is imported lazily so a missing install only soft-fails this
        step (snapshots.json refresh still works without it).
      - Skips any source whose lowres counterpart already exists. Delete the
        lowres file by hand to force a regeneration on the next run.
      - PNGs save with optimize=True; JPEGs save at quality 85 — both are
        reasonable defaults that don't bloat the repo with massive screenshots.
      - Resampling: LANCZOS, which is the highest-quality downscale Pillow
        offers and is the right pick for game-map screenshots (preserves
        crisp province borders better than bilinear/bicubic)."""
    try:
        from PIL import Image
    except ImportError:
        print("  (lowres generation skipped — install Pillow with `pip install Pillow`)")
        return 0, 0

    # Paradox map screenshots are huge (16384x8192 = 134M pixels) and trip
    # Pillow's decompression-bomb safety guard (~89M default). These are our
    # own trusted files, so disable the guard for this script.
    Image.MAX_IMAGE_PIXELS = None

    lowres_dir = maps_dir / "lowres"
    generated = 0
    total = 0

    for src in sorted(maps_dir.iterdir()):
        if src.is_dir() or src.suffix.lower() not in IMAGE_EXTS:
            continue
        total += 1
        dst = lowres_dir / (src.stem + LOWRES_EXT)
        if dst.exists():
            continue

        lowres_dir.mkdir(exist_ok=True)
        # Large game-map PNGs (e.g. 16384x8192) take a moment — surface that.
        print(f"  + generating lowres: {src.name} ...", end=" ", flush=True)
        try:
            with Image.open(src) as img:
                w, h = img.size
                new_size = (max(1, int(w * LOWRES_SCALE)),
                            max(1, int(h * LOWRES_SCALE)))
                # Resampling on indexed/palette source requires RGB conversion
                # first — LANCZOS produces non-palette colors anyway.
                if img.mode in ("P", "1"):
                    img = img.convert("RGBA" if "transparency" in img.info else "RGB")
                resized = img.resize(new_size, Image.Resampling.LANCZOS)
                # method=6 = slowest/highest-compression — fine for an offline
                # tool; usually wins another 5-15% file size vs default.
                resized.save(dst, "WEBP", quality=LOWRES_QUALITY, method=6)
        except Exception as exc:
            # Don't let one bad file abort the whole refresh — log and move on.
            print(f"FAILED ({exc})")
            continue
        out_kb = dst.stat().st_size // 1024
        print(f"{w}x{h} -> {new_size[0]}x{new_size[1]}  ({out_kb} KB)")
        generated += 1

    return generated, total


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
        campaign = snap_path.parent.parent.name
        # Generate any missing lowres companions FIRST so the snapshots refresh
        # below sees the new files and wires image_lowres pointers for them.
        gen, total = generate_missing_lowres(maps_dir)
        if gen:
            print(f"{campaign}: generated {gen} lowres file(s) (of {total} source map(s))")
        snaps, skipped = refresh_one(snap_path, maps_dir)
        msg = f"{campaign}: {len(snaps)} snapshot(s)"
        if skipped:
            msg += f"  (skipped, no date in filename: {', '.join(skipped)})"
        print(msg)
        touched += 1
    if touched == 0:
        print("No campaign folders found (expected */data/snapshots.json)")


if __name__ == "__main__":
    main()
