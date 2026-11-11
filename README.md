# OoT Character Select

A mod for *The Legend of Zelda: Ocarina of Time* on Switch 2 (game version 1.1.1) that adds a **Characters** page to the − menu. Pick any of 99 characters, Link and the game's own NPCs, and play as them.

Not affiliated with or endorsed by Nintendo. No game files are included; you need your own copy of the game. The licence (MIT, see `LICENSE`) applies to the source code in this repository.

**Status:** first test version (v0.1.0). Back up your save before trying it.

## What it does

- **A fourth tab in the − menu.** Characters sits left of Key Items; L / ZL from Key Items opens it, and the header shows four tabs.
- **A character picker.** Pages of 18 slots with a portrait for each character, in the look of the game's equipment page. D-pad or left stick moves, ZL / ZR turn pages, A picks.
- **A live preview.** The picked character slides in on the right of the page, with the view zoomed to fit tall and short characters.
- **You play as the pick.** When the menu closes, the character you picked replaces Link in the field, at the same place, facing the same way, with the camera where it was. Link's animations drive every character at that character's own proportions.
- **The pick is saved** with the save file.

## Install

The mod is five folders for the emulator's mods folder.

1. Close the game.
2. In the emulator's game list, right-click the game and choose "Open Mod Data Location". This opens `...\load\0400486024DEC000`.
3. Copy the five folders into it with their names unchanged (the emulator layers mod folders in name order, and the order matters):
   - `Character field swap`
   - `Character models`
   - `Characters page`
   - `Header four tabs`
   - `NPC characters`
4. Start the game. Each folder can be switched off under the game's properties, Add-Ons.

To remove the mod, delete the five folders.

Good to know:

- **Game version 1.1.1 only.** The code patches are tied to that build (build id `E0F3CCFAF50065AE4EA01EB5AEB13B1A45DA3348`). On another version, switch the folders off.
- **Button prompt mods conflict.** This mod replaces the same layout texture file, so only one of the two can be on.
- **`Character field swap` can be off by itself.** The picked character then shows in the field after save, return to title and Continue.
- **Known gaps:** most NPCs are listed under their file names, and swapping while riding is not handled.

## Is it safe to install?

A mod package from this project holds data files only: changed game files (`.zs`, `.ainb`) and four small patches to the game's own code (`.ips`), which run inside the emulated game. Nothing in it runs on your computer: no programs, scripts or installers. If a package claiming to be this mod contains anything else, do not use it.

Packages made by the author, and the SHA-256 sum of each zip:

| Package | SHA-256 |
| --- | --- |
| `OoT-Character-Select-v0.1.0.zip` | `4b11117ad7ef6849049c48c2c4e41ddca3f776efe6d7ac08968ca23b378b25de` |

Check a zip you were sent with `certutil -hashfile <zip> SHA256` (Windows) before unpacking it.

Each package comes with `SHA256SUMS.txt` (the sum of every file) and `patch-listings` (every code patch as readable assembly, written by the same build that made the patch). The build is deterministic: the same sources and the same game files give the same bytes, so a package can be rebuilt from this repository and compared. `tools/package.py` makes the package and refuses any file type other than the data files above.

## Build from source

You need Windows, Python 3.11, and from your own copy of the game (version 1.1.1): the extracted romfs and the executable image as dumped from memory at base `0x80000000`.

```
python -m venv venv
venv\Scripts\pip install -r requirements.txt
set OOT_ROMFS=C:\path\to\romfs
set OOT_IMAGE=C:\path\to\image.bin
venv\Scripts\python mods\build.py
```

The mod folders are written to `build\`. `--install` also copies them into `%APPDATA%\eden\load\0400486024DEC000`.

Two inputs are not in this repository, and the full build currently expects both:

- **Portraits.** The pictures in the picker's slots are captured from the running game (`tools/npcshots.py`, `tools/npccutout.py`) into `build-npc\portraits\cut-fit`. Without them, a slot shows a plain silhouette.
- **A third-party model.** An earlier version offered another author's N64 Link model, read from `assets\models`. It is not offered in the picker and not part of the package, but the build still reads it. Making the build run without it is on the list.

## How it works

Everything is built by Python scripts from the untouched game files; nothing is edited by hand. `docs/NOTES.md` is the full working record (every format, address and dead end); this is the short version.

- **Mod folders.** The emulator overlays `romfs` files and applies `exefs` `.ips` patches per folder. The build writes each changed file, plus one combined resource size table, since the game refuses files larger than their table entry.
- **The page.** The game's menus are node graphs (`.ainb`). The Characters page is a graph of our own (`Sequence/CharacterMenu.module.ainb`) with its own screen and layout, hooked into the − menu's graphs. `tools/ainbx.py` reads and writes the graphs.
- **The layout.** The game's layout format (`.buil`) was mapped in full; `tools/layout.py` and `tools/uiedit.py` read, edit and write it. The picker is built from the game's own inventory slot parts.
- **The header.** Four tabs instead of three needs a code patch: the header's builder is given a new preset.
- **Character models.** A saved number says who is picked. A hook on the game's model path formats sends the player's model load to the picked character's file.
- **NPCs as the player.** Each NPC's model file gets a rebuilt skeleton (`tools/npcrig.py`, `tools/bfres.py`): Link's bone names at the NPC's proportions, plus every bone and material the player's code looks up by name.
- **The slide and the zoom.** The menu holds the old and the new character at once; hooks in the menu camera's update move them, scale the view per character and keep the shadow range matched. The hooks are emulated at build time (unicorn) through whole transitions.
- **Portraits.** Each character is captured in game on a black and on a white backdrop; the difference gives the transparency. The pictures are encoded as ASTC and added to a copy of the layout texture archive (`tools/bntxwrite.py`).
- **The field swap.** The field Link is a "resident actor". The swap follows the game's own age change without the area load: the player is deleted, the game's own node creates the new one, and he is woken where the old one stood. The two steps the game has no node for live in the code of a node class no graph uses, so no live game code is changed.
- **Code patches.** `tools/exepatch.py` assembles patches (keystone), places new code in unused space of the executable and writes IPS32. Every patch is checked against the bytes it expects and against the other patches.

## Layout of the repository

- `mods/build.py`: the build (`mods/ui_sandbox.py`, `mods/npc_characters.py` and `mods/field_swap.py` are used by it as libraries and can each build a test variant).
- `tools/`: file format readers and writers, the patcher, the packager, and read-only debug helpers.
- `docs/NOTES.md`: the working notes.

## Credits

Mod and tools by snaildev99. *The Legend of Zelda: Ocarina of Time* and its characters belong to Nintendo.
