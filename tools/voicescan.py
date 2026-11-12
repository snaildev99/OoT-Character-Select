"""Which voice each of the 100 characters has in the game, and how much of it.

    venv-voice\Scripts\python.exe -I tools\voicescan.py      -> build-voice\scan.txt, scan.json

Per character of the ring (`build-npc\characters.txt`): the actors that use
its model, the voice type their `VoiceParam` names, and for that type the
recorded clips (`Sound/Resource/Stream/NV_...`): how many, how many seconds
after trimming, the cutscene lines apart (`Dm_` / `Ev_`), and the median pitch
against child Link's (Praat, floor 60 Hz so deep voices count). Read only.
"""
import collections, glob, json, os, re, sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bwav, nxfmt, voiceai

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROMFS = os.path.dirname(os.path.dirname(os.path.dirname(bwav.STREAM)))
OUT = os.path.join(ROOT, "build-voice")
LANG = "USen"


def ring():
    """[(number, label, model)] from the NPC session's list."""
    rows = []
    for line in open(os.path.join(ROOT, "build-npc", "characters.txt"), encoding="utf-8"):
        m = re.match(r"\s*(\d+)\s+(.+?)\s{2,}C\d+\s+(\S+)", line)
        if m and int(m.group(1)) < 1000:      # above: the NPC session's experiments
            rows.append((int(m.group(1)), m.group(2), m.group(3)))
    return rows


def actors():
    """{actor: (model, voice type or None, sound user or None)} for every actor pack."""
    out = {}
    for p in glob.glob(os.path.join(ROMFS, "Pack", "Actor", "*.pack.zs")):
        a = os.path.basename(p)[:-8]
        files = nxfmt.sarc_files(nxfmt.read(p))

        def comps(name):
            d = files.get("Actor/%s.engine__actor__ActorParam.bgyml" % name)
            if d is None:
                return {}
            r = nxfmt.Byml(d).root
            c = comps(os.path.basename(r["$parent"]).split(".")[0]) if r.get("$parent") else {}
            c.update(r.get("Components") or {})
            return c

        def sub(ref):
            d = files.get(ref.lstrip("?")) if ref else None
            return nxfmt.Byml(d).root if d else {}

        c = comps(a)
        if "ModelInfoRef" not in c:
            continue
        voice = os.path.basename(sub(c.get("VoiceRef")).get("VoiceTypeInfoBgymlPath", "")).split(".")[0]
        out[a] = (sub(c["ModelInfoRef"]).get("FmdbName"), voice or None, sub(c.get("SLinkRef")).get("UserName"))
    return out


def pitch(xs, rate):
    import parselmouth
    f = []
    for x in xs:
        if len(x) < rate * 0.08:
            continue
        snd = parselmouth.Sound(x, sampling_frequency=rate)
        g = snd.to_pitch_ac(time_step=0.005, pitch_floor=60, pitch_ceiling=900).selected_array["frequency"]
        f.append(g[g > 0])
    f = np.concatenate(f) if f else np.zeros(0)
    return (float(np.median(f)), len(f) * 0.005) if len(f) else (0.0, 0.0)


def all_types(_cache=[]):
    if not _cache:
        _cache.append([os.path.basename(p).split(".")[0]
                       for p in glob.glob(os.path.join(ROMFS, "Voice", "VoiceTypeInfo", "*.bgyml*"))])
    return _cache[0]


def voice_type(voice):
    prefix = "NV_%s_%s_" % (LANG, voice) if voice.startswith("Custom_") else "NV_%s_" % voice
    names = sorted(os.path.basename(p)[:-5] for p in glob.glob(os.path.join(bwav.STREAM, prefix + "*.bwav")))
    # `Dampe` must not take `Dampe_Ghost`'s clips: a longer type name that fits owns the clip
    longer = ["NV_%s_" % t for t in all_types() if t != voice and t.startswith(voice + "_")]
    names = [n for n in names if not n.startswith(tuple(longer))] if longer else names
    plain, scene, kinds = [], [], collections.Counter()
    for n in names:
        s, rate = bwav.clip(n)
        x = voiceai.trimmed(s[0].astype(np.float64), rate)
        tail = n[len(prefix):]
        (scene if tail.startswith(("Dm_", "Ev_")) else plain).append(x)
        if not tail.startswith(("Dm_", "Ev_")):
            kinds[re.sub(r"[_\d]+$", "", tail)] += 1
    hz, voiced = pitch(plain + scene, 48000)
    return {"clips": len(plain), "seconds": round(sum(map(len, plain)) / 48000, 1),
            "scene_clips": len(scene), "scene_seconds": round(sum(map(len, scene)) / 48000, 1),
            "hz": round(hz), "voiced": round(voiced, 1), "kinds": dict(kinds)}


def main():
    acts = actors()
    by_model = collections.defaultdict(list)
    for a, (model, voice, user) in acts.items():
        by_model[model].append(a)
    rows, types = [], {}
    for number, label, model in ring():
        mine = sorted(by_model.get(model, []))
        voices = collections.Counter(acts[a][1] for a in mine if acts[a][1])
        for v in voices:
            if v not in types:
                types[v] = voice_type(v)
                print(v, types[v]["clips"], types[v]["seconds"], types[v]["hz"], flush=True)
        rows.append({"number": number, "label": label, "model": model, "actors": mine,
                     "voices": dict(voices)})
    json.dump({"characters": rows, "types": types}, open(os.path.join(OUT, "scan.json"), "w"), indent=1)
    semis = lambda hz: 12 * np.log2(hz / 363.0) if hz else float("nan")
    with open(os.path.join(OUT, "scan.txt"), "w", encoding="utf-8") as f:
        f.write("Voices of the 100 characters (tools/voicescan.py). Pitch: semitones against child Link (363 Hz).\n"
                "clips / s = talk reactions; +scene = cutscene lines (Dm_, Ev_).\n\n")
        f.write("%3s  %-26s %-30s %5s %6s %7s %6s %6s  %s\n" % ("no", "character", "voice type", "clips", "s", "+scene", "Hz", "semis", "actors"))
        for r in rows:
            if not r["voices"]:
                f.write("%3d  %-26s %-30s %s\n" % (r["number"], r["label"], "-", "(%d actors, none with a voice)" % len(r["actors"])))
            for i, (v, n) in enumerate(sorted(r["voices"].items(), key=lambda kv: -kv[1])):
                t = types[v]
                f.write("%3s  %-26s %-30s %5d %6.1f %7.1f %6d %+6.1f  %d of %d\n" % (
                    r["number"] if not i else "", r["label"] if not i else "", v, t["clips"], t["seconds"],
                    t["scene_seconds"], t["hz"], semis(t["hz"]), n, len(r["actors"])))
        users = collections.defaultdict(list)
        for r in rows:
            for v in r["voices"]:
                users[v].append(r["label"])
        f.write("\nVoice types and who has them:\n")
        for v in sorted(users, key=lambda v: (not v.startswith("Custom_"), v)):
            f.write("  %-30s %s\n" % (v, ", ".join(users[v])))
    print("characters:", len(rows), "with a voice:", sum(bool(r["voices"]) for r in rows), "types:", len(types))


if __name__ == "__main__":
    main()
