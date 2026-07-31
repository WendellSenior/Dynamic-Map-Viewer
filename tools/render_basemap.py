"""Render a plain land/sea basemap from a HoI4 (or HoI4 mod) province bitmap.

Purpose: sanity-check derived coordinates. HoI4 ships no pin positions, so
tools/parse_hoi4_reference.py computes them from map/provinces.bmp — and
before you have real in-game screenshots there is nothing to check that work
against. This renders the same bitmap the coordinates came from, at the game's
native size, so a campaign can display it as a snapshot and you can see
whether Berlin actually lands on Berlin.

Useful for a brand-new game or mod: drop the output into a campaign's
data/maps/, run tools/refresh_snapshots.py, and the viewer has a real map to
pin against on day one. Replace it with proper screenshots later.

Output is deliberately flat and unbranded — land, water, and province edges —
so event pins stay readable on top of it.

Usage:
    python tools/render_basemap.py --out hoi4-kr-test/data/maps/1936_January_01.png
    python tools/render_basemap.py --mod kaiserreich --out <path>
"""

import argparse
import sys
from pathlib import Path

import parse_hoi4_reference as P

# Flat, low-contrast palette: the map is a backdrop for event pins, not the
# subject. Land stays light so the viewer's coloured dots read clearly on it.
COLOUR_LAND    = (222, 216, 201)
COLOUR_SEA     = (168, 196, 216)
COLOUR_LAKE    = (188, 212, 228)
COLOUR_UNKNOWN = (200, 200, 200)
COLOUR_BORDER  = (176, 168, 152)
COLOUR_COAST   = (120, 132, 144)


def main():
    ap = argparse.ArgumentParser(description="Render a HoI4 land/sea basemap")
    ap.add_argument("--game-dir", default="", help="path to the HoI4 install")
    ap.add_argument("--mod", default="",
                    help="mod overlay: known name (kaiserreich), workshop id, or path")
    ap.add_argument("--out", required=True, help="output PNG path")
    ap.add_argument("--no-borders", action="store_true",
                    help="skip province outlines (flat land/sea only)")
    args = ap.parse_args()

    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        sys.exit("ERROR: needs numpy + Pillow (pip install numpy Pillow).")

    game_dir = P.resolve_game_dir(args.game_dir)
    mod_dir, _default_game = P.resolve_mod_dir(args.mod)
    files = P.GameFiles(game_dir, mod_dir)

    P.log(f"HoI4 install: {game_dir}")
    if mod_dir:
        P.log(f"Mod overlay : {mod_dir}")

    P.log("Parsing definition.csv ...")
    colour_to_id, kinds = P.parse_definitions(files.file("map/definition.csv"))

    P.log("Rasterising provinces.bmp ...")
    labels, _counts = P.compute_province_pixels(
        files.file("map/provinces.bmp"), colour_to_id)
    height, width = labels.shape
    P.log(f"  {width}x{height}")

    # province id -> terrain class, as a lookup indexed by id.
    max_pid = int(labels.max())
    kind_lut = np.zeros(max_pid + 1, dtype=np.uint8)  # 0 unknown, 1 land, 2 sea, 3 lake
    for pid, kind in kinds.items():
        if 0 <= pid <= max_pid:
            kind_lut[pid] = {"land": 1, "sea": 2, "lake": 3}.get(kind, 0)
    classes = kind_lut[labels]

    P.log("Painting ...")
    img = np.zeros((height, width, 3), dtype=np.uint8)
    img[:] = COLOUR_UNKNOWN
    for value, colour in ((1, COLOUR_LAND), (2, COLOUR_SEA), (3, COLOUR_LAKE)):
        img[classes == value] = colour

    if not args.no_borders:
        # A pixel is an edge when the province to its right or below differs.
        # Land/water edges get a stronger colour so coastlines read as
        # coastlines rather than as just another province line.
        diff = np.zeros((height, width), dtype=bool)
        diff[:, :-1] |= labels[:, :-1] != labels[:, 1:]
        diff[:-1, :] |= labels[:-1, :] != labels[1:, :]

        water = (classes == 2) | (classes == 3)
        wdiff = np.zeros((height, width), dtype=bool)
        wdiff[:, :-1] |= water[:, :-1] != water[:, 1:]
        wdiff[:-1, :] |= water[:-1, :] != water[1:, :]

        img[diff & ~wdiff] = COLOUR_BORDER
        img[wdiff] = COLOUR_COAST

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(img, "RGB").save(out, optimize=True)
    P.log(f"Wrote {out}  ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
