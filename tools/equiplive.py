"""READ ONLY (customization session, written offline 2026-11-14 from the game's code; README, player models, "The
equipment's 21 handles"). The equipment of the field character and of the menu character from the running game,
through the debug server (PLAY-debug.bat): every one of the 21 handles with its actor's name, the request arrays, the
names the component keeps, the pouch's name strings and the raw record of the GameData struct PlayerStatus. Nothing is
written to the game.

    venv\\Scripts\\python.exe -I tools\\equiplive.py

What to have on screen, one run each (the output says which handle each piece is in):
  child:  sword drawn + Deku shield raised; then the slingshot out; then a bottle or a Deku stick in hand; a mask on;
  adult:  Goron or Zora tunic + Iron or Hover boots + gauntlets; bow out; hookshot out; hammer out; Biggoron's sword;
          Mirror shield; the Lens of Truth on; the Stone of Agony owned.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from live import Live, MENU_GLOBAL, GAMEDATA_GLOBAL  # noqa: E402

POUCH_VAR = 0x84E98E28          # GOT 0x84c9e0d0 points here; [this] = the pouch
DYNAMIC = ["Weapon", "SubWeapon", "ThirdWeapon", "Bow", "Shield", "Tool", "SubTool", "Helmet"]
STATIC = ["Head", "Upper", "Lower", "Hand", "Sheath", "SubSheath", "ThirdSheath", "Quiver", "Accessory1", "Accessory2",
          "Accessory3", "Extra", "Accessory4"]
SLOTS = ["Weapon", "SubWeapon", "ThirdWeapon", "Sheath", "Quiver", "Bow", "Shield", "ArmorHead", "ArmorUpper", "ArmorLower",
         "ArmorHand", "Tool", "SubTool", "Accessory1", "Accessory2", "Accessory3", "Accessory4", "Extra"]
SHOWN, BIT = 0x4AC, 0x10000


def cstr(lv, a, n=64):
    if not a:
        return ""
    try:
        return lv.d.read(a, n).split(b"\0")[0].decode("latin-1")
    except Exception as e:      # noqa: BLE001
        return "(unreadable: %s)" % e


def handle(lv, at):
    link, hid = lv.q(at + 8), lv.u(at + 0x10)
    if hid == 0xFFFFFFFF or not link:
        return None
    if lv.u(link + 0x48) != hid:
        return "(stale link)"
    a = lv.q(link + 0x40)
    if not a:
        return "(no actor)"
    return "%-34s state %d %s  actor %x" % (cstr(lv, lv.q(a + 0x20)), lv.u(a + 0x2C), "shown " if lv.u(a + SHOWN) & BIT else "hidden", a)


def show(lv, title, equip):
    owner = lv.q(equip + 0x18)
    print("%s: equipment component %x, owner %x %s" % (title, equip, owner, cstr(lv, lv.q(owner + 0x20))))
    for k, name in enumerate(DYNAMIC + STATIC):
        h = handle(lv, equip + 0x20 + 0x18 * k)
        if h:
            print("   handle %2d %-12s %s" % (k, name, h))
    print("   requests pending mask %#x" % lv.u(equip + 0x12D0))
    for s, name in enumerate(SLOTS):
        state, req = lv.u(equip + 0x12D4 + 8 * s), lv.q(equip + 0x10B0 + 8 * s)
        if state or req:
            print("   slot %2d %-11s state %d request %x" % (s, name, state, req))
    for what, off in (("head armour name", 0x1140), ("sheath name", 0x1190), ("quiver name", 0x11E0)):
        print("   %-17s %r" % (what, cstr(lv, lv.q(equip + off))))
    print("   drawn / sheathed bytes +0x13bc: %s   +0x13c4: %s" % (lv.d.read(equip + 0x13BC, 8).hex(), lv.d.read(equip + 0x13C4, 21).hex()))


def main():
    lv = Live()
    print("main shifted by %#x" % lv.delta)
    pouch = lv.q(POUCH_VAR + lv.delta)
    print("pouch %x; is child (byte [menu global] + 0x5c0): %d" % (pouch, lv.d.read(lv.q(MENU_GLOBAL + lv.delta) + 0x5C0, 1)[0]))
    for off in (0x80, 0xB0, 0xE0, 0x110, 0x140, 0x170):
        print("   pouch + %#5x: %r" % (off, cstr(lv, lv.q(pouch + off))))
    equip = lv.q(pouch + 0x470)
    if equip:
        show(lv, "the pouch's target (field character)", equip)
    mm = lv.q(lv.q(MENU_GLOBAL + lv.delta) + 0x4E8)
    link, hid = lv.q(mm + 0x28 + 0x10), lv.u(mm + 0x28 + 0x18)
    if link and hid != 0xFFFFFFFF and lv.u(link + 0x48) == hid:
        a = lv.q(link + 0x40)
        comps = lv.q(a + 0x270)
        if comps and lv.q(comps + 0x200):
            show(lv, "the menu character", lv.q(comps + 0x200))
    # GameData: the struct PlayerStatus (hash 0x060890b0): map at +0xc20 (hash, index), capacity +0xc2c, records +0xc18
    # (0x20 bytes each), count +0xc10. The record is printed raw: where its String64 members live is not read out yet.
    gm = lv.q(GAMEDATA_GLOBAL + lv.delta)
    m, cap, recs, count = lv.q(gm + 0xC20), lv.u(gm + 0xC2C), lv.q(gm + 0xC18), lv.u(gm + 0xC10)
    h = 0x060890B0
    i = h % cap
    for _ in range(cap):
        eh, idx = struct.unpack("<II", lv.d.read(m + 8 * i, 8))
        if eh == h:
            rec = recs + 0x20 * idx
            raw = lv.d.read(rec, 0x20)
            print("PlayerStatus: record %d of %d at %x: %s" % (idx, count, rec, raw.hex()))
            for k in range(4):
                p = struct.unpack_from("<Q", raw, 8 * k)[0]
                if 0x1000000 < p < 0x800000000000:
                    try:
                        print("   [+%#x] -> %s" % (8 * k, lv.d.read(p, 0x60).hex()))
                    except Exception:       # noqa: BLE001
                        pass
            break
        if eh == 0:
            print("PlayerStatus: not in the struct map")
            break
        i = (i + 1) % cap


if __name__ == "__main__":
    main()
