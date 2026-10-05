Bundled caption fonts (engine/ac/captions/fonts/*.ttf)
=====================================================

All fonts are from the google/fonts repository (github.com/google/fonts, ofl/ folder) and licensed under the
SIL Open Font License 1.1 (full text: OFL.txt and <Family>-OFL.txt in this folder, https://openfontlicense.org).

They were built by proto/caption_render/make_fonts.py (first 17 files) and build_fonts.py in this folder (the
33 "Gaya Pro" files added 2026-10-05): variable fonts instanced to static weights and every file given a unique
family name ("Montserrat Black", "Rubik Black", ...) so libass selects them deterministically. Under the OFL these
renamed/instanced files are Modified Versions; they are used privately inside AutoCut BOT and are never
redistributed. Do not ship them in a public product without restoring the original names. Fonts that carry a
Reserved Font Name and were added in 2026-10 (Righteous, Paytone One, Passion One, Russo One, Knewave, Gochi Hand,
Abril Fatface) are copied byte-for-byte, unmodified. Lilita One and Titan One (first set) were renamed by the
proto script and also carry Reserved Font Names.

File                          Family name                    Copyright / licence file
Anton.ttf                     Anton                          2020 The Anton Project Authors (github.com/googlefonts/AntonFont)
ArchivoBlack.ttf              Archivo Black                  2017 The Archivo Black Project Authors (github.com/Omnibus-Type/ArchivoBlack)
Bangers.ttf                   Bangers                        2010 The Bangers Project Authors (github.com/googlefonts/bangers)
BebasNeue.ttf                 Bebas Neue                     2019 The Bebas Neue Project Authors (github.com/dharmatype/Bebas-Neue)
InterSemiBold/ExtraBold/Black Inter SemiBold/ExtraBold/Black 2016 The Inter Project Authors (github.com/rsms/inter)
LilitaOne.ttf                 Lilita One                     2011 Juan Montoreano, Reserved Font Name "Lilita One"
MontserratBold/ExtraBold/Black Montserrat Bold/ExtraBold/Black 2011 The Montserrat Project Authors (github.com/JulietaUla/Montserrat)
PlusJakartaSansBold/ExtraBold Plus Jakarta Sans Bold/ExtraBold 2020 The Plus Jakarta Sans Project Authors (github.com/tokotype/PlusJakartaSans)
PoppinsBold/ExtraBold/Black   Poppins Bold/ExtraBold/Black   2020 The Poppins Project Authors (github.com/itfoundry/Poppins)
TitanOne.ttf                  Titan One                      2011 Rodrigo Fuenzalida, Reserved Font Name "Titan One"
--- added 2026-10-05 (build_fonts.py; licence text per family in <Family>-OFL.txt) ---
RubikBold/Black               Rubik Bold/Black               Rubik-OFL.txt (instanced from Rubik[wght])
NunitoExtraBold/Black         Nunito ExtraBold/Black         Nunito-OFL.txt (instanced)
FredokaSemiBold/Bold          Fredoka SemiBold/Bold          Fredoka-OFL.txt (instanced, wdth 100)
Baloo2ExtraBold               Baloo 2 ExtraBold              Baloo2-OFL.txt (instanced)
BarlowCondensedBold/ExtraBold/Black  Barlow Condensed ...    BarlowCondensed-OFL.txt (renamed static)
OswaldSemiBold/Bold           Oswald SemiBold/Bold           Oswald-OFL.txt (instanced)
ArchivoExtraBold              Archivo ExtraBold              Archivo-OFL.txt (instanced, wdth 100)
SoraBold/ExtraBold            Sora Bold/ExtraBold            Sora-OFL.txt (instanced)
OutfitBold/Black              Outfit Bold/Black              Outfit-OFL.txt (instanced)
ManropeBold/ExtraBold         Manrope Bold/ExtraBold         Manrope-OFL.txt (instanced)
KanitBold/Black               Kanit Bold/Black               Kanit-OFL.txt (renamed static)
Righteous.ttf                 Righteous                      Righteous-OFL.txt (unmodified, RFN "Righteous")
PaytoneOne.ttf                Paytone One                    PaytoneOne-OFL.txt (unmodified, RFN "Paytone")
PassionOne.ttf                Passion One                    PassionOne-OFL.txt (unmodified, RFN "Passion")
RussoOne.ttf                  Russo One                      RussoOne-OFL.txt (unmodified, RFN "Russo")
BlackOpsOne.ttf               Black Ops One                  BlackOpsOne-OFL.txt (unmodified)
Bungee.ttf                    Bungee                         Bungee-OFL.txt (unmodified)
Pacifico.ttf                  Pacifico                       Pacifico-OFL.txt (unmodified)
CaveatBrush.ttf               Caveat Brush                   CaveatBrush-OFL.txt (unmodified)
Knewave.ttf                   Knewave                        Knewave-OFL.txt (unmodified, RFN "Knewave")
GochiHand.ttf                 Gochi Hand                     GochiHand-OFL.txt (unmodified, RFN "Gochi")
DMSerifDisplay.ttf            DM Serif Display               DMSerifDisplay-OFL.txt (unmodified)
AbrilFatface.ttf              Abril Fatface                  AbrilFatface-OFL.txt (unmodified, RFN "Abril")

Not bundled: Luckiest Guy and Permanent Marker (Apache 2.0 in google/fonts, outside the OFL-only rule).

Keep only .ttf files directly in fonts/: libass logs "Error opening memory font" for any other file in fontsdir.
(Each ffmpeg run only sees a hard-linked subset of the files its ASS uses: layout.fontset_dir.)
