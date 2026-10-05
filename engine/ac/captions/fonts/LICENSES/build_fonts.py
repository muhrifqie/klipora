"""Rebuild the caption fonts added in the "Gaya Pro" round (2026-10-05) from google/fonts sources.

Usage: python build_fonts.py <folder with the downloaded google/fonts files>
Sources: https://github.com/google/fonts/raw/main/ofl/<family>/<file> (OFL 1.1, OFL.txt per family copied here
as <Family>-OFL.txt). Variable fonts are instanced to static weights with fontTools and every output file gets a
UNIQUE family name ("Rubik Black", ...) with subfamily Regular and the right usWeightClass, so libass selects it
by family name alone and never fakes bold (same recipe as proto/caption_render/make_fonts.py). Fonts that carry a
Reserved Font Name are copied unmodified. This script is not used at runtime (libass only reads the .ttf files
directly in fonts/).
"""
import shutil
import sys
from pathlib import Path

DST = Path(__file__).resolve().parents[1]

# (source file, axis location, new family name, weight class)
VARIABLE = [
    ("Rubik[wght].ttf", {"wght": 700}, "Rubik Bold", 700),
    ("Rubik[wght].ttf", {"wght": 900}, "Rubik Black", 900),
    ("Nunito[wght].ttf", {"wght": 800}, "Nunito ExtraBold", 800),
    ("Nunito[wght].ttf", {"wght": 900}, "Nunito Black", 900),
    ("Fredoka[wdth,wght].ttf", {"wght": 600, "wdth": 100}, "Fredoka SemiBold", 600),
    ("Fredoka[wdth,wght].ttf", {"wght": 700, "wdth": 100}, "Fredoka Bold", 700),
    ("Baloo2[wght].ttf", {"wght": 800}, "Baloo 2 ExtraBold", 800),
    ("Oswald[wght].ttf", {"wght": 600}, "Oswald SemiBold", 600),
    ("Oswald[wght].ttf", {"wght": 700}, "Oswald Bold", 700),
    ("Archivo[wdth,wght].ttf", {"wght": 800, "wdth": 100}, "Archivo ExtraBold", 800),
    ("Sora[wght].ttf", {"wght": 700}, "Sora Bold", 700),
    ("Sora[wght].ttf", {"wght": 800}, "Sora ExtraBold", 800),
    ("Outfit[wght].ttf", {"wght": 700}, "Outfit Bold", 700),
    ("Outfit[wght].ttf", {"wght": 900}, "Outfit Black", 900),
    ("Manrope[wght].ttf", {"wght": 700}, "Manrope Bold", 700),
    ("Manrope[wght].ttf", {"wght": 800}, "Manrope ExtraBold", 800),
]
# static files renamed to a unique family (no Reserved Font Name on these)
RENAMED = [
    ("BarlowCondensed-Bold.ttf", "Barlow Condensed Bold", 700),
    ("BarlowCondensed-ExtraBold.ttf", "Barlow Condensed ExtraBold", 800),
    ("BarlowCondensed-Black.ttf", "Barlow Condensed Black", 900),
    ("Kanit-Bold.ttf", "Kanit Bold", 700),
    ("Kanit-Black.ttf", "Kanit Black", 900),
]
# copied byte-for-byte (single weight, family name already unique, some carry Reserved Font Names)
COPIED = [
    ("Righteous-Regular.ttf", "Righteous.ttf"), ("PaytoneOne-Regular.ttf", "PaytoneOne.ttf"),
    ("PassionOne-Regular.ttf", "PassionOne.ttf"), ("RussoOne-Regular.ttf", "RussoOne.ttf"),
    ("BlackOpsOne-Regular.ttf", "BlackOpsOne.ttf"), ("Pacifico-Regular.ttf", "Pacifico.ttf"),
    ("CaveatBrush-Regular.ttf", "CaveatBrush.ttf"), ("Bungee-Regular.ttf", "Bungee.ttf"),
    ("Knewave-Regular.ttf", "Knewave.ttf"), ("DMSerifDisplay-Regular.ttf", "DMSerifDisplay.ttf"),
    ("GochiHand-Regular.ttf", "GochiHand.ttf"), ("AbrilFatface-Regular.ttf", "AbrilFatface.ttf"),
]
LICENSES = {"archivo": "Archivo", "baloo2": "Baloo2", "barlowcondensed": "BarlowCondensed",
            "blackopsone": "BlackOpsOne", "bungee": "Bungee", "caveatbrush": "CaveatBrush",
            "dmserifdisplay": "DMSerifDisplay", "fredoka": "Fredoka", "gochihand": "GochiHand", "kanit": "Kanit",
            "knewave": "Knewave", "manrope": "Manrope", "nunito": "Nunito", "oswald": "Oswald", "outfit": "Outfit",
            "pacifico": "Pacifico", "passionone": "PassionOne", "paytoneone": "PaytoneOne",
            "righteous": "Righteous", "rubik": "Rubik", "russoone": "RussoOne", "sora": "Sora",
            "abrilfatface": "AbrilFatface"}


def rename(font, family, weight):
    """One family per file, subfamily Regular, so libass matches by family name alone."""
    name = font["name"]
    ps = family.replace(" ", "")
    for rec in list(name.names):
        if rec.nameID in (16, 17, 21, 22, 25):  # typographic family/subfamily, WWS, variations prefix
            name.removeNames(nameID=rec.nameID)
    for nid, val in ((1, family), (2, "Regular"), (3, f"{ps};autocut"), (4, family), (6, ps)):
        name.setName(val, nid, 3, 1, 0x409)
        name.setName(val, nid, 1, 0, 0)
    font["OS/2"].usWeightClass = weight
    font["OS/2"].fsSelection = (font["OS/2"].fsSelection & ~0b1100001) | 0b1000000  # REGULAR, not bold/italic
    font["head"].macStyle = 0
    if "STAT" in font:
        del font["STAT"]


def main(src):
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer
    src = Path(src)
    for f, loc, fam, w in VARIABLE:
        st = instancer.instantiateVariableFont(TTFont(src / f), loc)
        rename(st, fam, w)
        st.save(DST / (fam.replace(" ", "") + ".ttf"))
        print("instanced", fam)
    for f, fam, w in RENAMED:
        t = TTFont(src / f)
        rename(t, fam, w)
        t.save(DST / (fam.replace(" ", "") + ".ttf"))
        print("renamed  ", fam)
    for f, out in COPIED:
        shutil.copyfile(src / f, DST / out)
        print("copied   ", out)
    for key, nice in LICENSES.items():
        p = src / f"{key}-OFL.txt"
        if p.is_file():
            shutil.copyfile(p, DST / "LICENSES" / f"{nice}-OFL.txt")


if __name__ == "__main__":
    main(sys.argv[1])
