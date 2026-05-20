"""Generate half-resolution variants of campaign maps for the viewer's Quality toggle.
Outputs go to <campaign>/data/maps/lowres/. Skipped if up-to-date."""

import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("downsample_maps: Pillow not installed — skipping. To enable, run: pip install pillow")
    sys.exit(0)


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
SCALE = 0.5
WEBP_QUALITY = 85


def downsample_one(src, dst):
    with Image.open(src) as img:
        if img.mode in ("P", "1"):
            img = img.convert("RGB")
        new_size = (max(1, int(img.width * SCALE)), max(1, int(img.height * SCALE)))
        img.thumbnail(new_size, Image.LANCZOS)
        dst.parent.mkdir(parents=True, exist_ok=True)
        img.save(dst, format="WEBP", quality=WEBP_QUALITY, method=6)


def main():
    repo_root = Path(__file__).resolve().parent.parent
    processed = 0
    fresh = 0
    for maps_dir in sorted(repo_root.glob("*/data/maps")):
        lowres_dir = maps_dir / "lowres"
        campaign = maps_dir.parent.parent.name
        for src in sorted(maps_dir.iterdir()):
            if src.suffix.lower() not in IMAGE_EXTS:
                continue
            dst = lowres_dir / (src.stem + ".webp")
            if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
                fresh += 1
                continue
            print(f"  {campaign}: {src.name} -> lowres/{src.name}")
            downsample_one(src, dst)
            processed += 1
    print(f"downsample_maps: {processed} new/updated, {fresh} up-to-date")


if __name__ == "__main__":
    main()
