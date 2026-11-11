"""Listening samples for the character voices: Link's clips reshaped towards an NPC.

Writes a few WAV files to `build-voice\\samples` (one per method, the same clips
in the same order) so the methods can be compared by ear, outside the game.

    python tools\\voicesamples.py

Methods:
- plain: resampling, the pitch slider of an audio editor. Pitch, speed and the
  mouth's resonances all move together (higher = shorter and smaller sounding).
- matched: Praat's "Change gender": pitch and resonances move separately, the
  length stays. Pitch is set from the measured median of the NPC's own clips.
"""
import os, sys

import numpy as np
import parselmouth
from parselmouth.praat import call

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bwav

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "build-voice", "samples")
GAP = 0.35          # seconds of silence between clips
PEAK = 0.8          # every file is scaled to this peak (one gain per file)

LINK = ["Vo_PlChild_SwordAttack_S_01", "Vo_PlChild_SwordAttack_S_03", "Vo_PlChild_SwordAttack_M_01",
        "Vo_PlChild_SwordAttack_M_03", "Vo_PlChild_SwordAttack_B_01", "Vo_PlChild_SwordAttack_B_04",
        "Vo_PlChild_JumpAttack_01", "Vo_PlChild_RollAttack_01", "Vo_PlChild_Damage_M_01",
        "Vo_PlChild_Jump_B_01"]


def plain(x, rate, semitones):
    """Resample: what a simple pitch change does."""
    ratio = 2 ** (semitones / 12)
    n = int(round(len(x) / ratio))
    return np.interp(np.arange(n) * ratio, np.arange(len(x)), x)


def matched(x, rate, semitones, formants):
    """Pitch by `semitones`, resonances by the ratio `formants`, same length."""
    snd = parselmouth.Sound(x.astype(np.float64), sampling_frequency=rate)
    pitch = snd.to_pitch_ac(time_step=0.005, pitch_floor=120, pitch_ceiling=1000)
    f = pitch.selected_array["frequency"]
    f = f[f > 0]
    if len(f) < 4:                       # nothing voiced to work on (a breath)
        return x
    median = float(np.median(f)) * 2 ** (semitones / 12)
    out = call(snd, "Change gender", 120, 1000, formants, median, 1.0, 1.0)
    return out.values[0]


def joined(clips, rate):
    gap = np.zeros(int(GAP * rate))
    parts = []
    for c in clips:
        parts += [c, gap]
    x = np.concatenate(parts)
    return x * (PEAK / max(1e-6, abs(x).max()))


def main():
    os.makedirs(OUT, exist_ok=True)
    link = [bwav.clip(a, "Player_Child") for a in LINK]
    rate = link[0][1]
    link = [s[0].astype(np.float64) for s, r in link]

    def save(name, clips):
        x = joined(clips, rate)
        bwav.write_wav(os.path.join(OUT, name + ".wav"), x[None, :], rate)
        print("%-40s %5.1f s" % (name, len(x) / rate))

    save("1 Link (original)", link)
    save("2 Saria - plain pitch +2", [plain(x, rate, 2) for x in link])
    save("3 Saria - plain pitch +4", [plain(x, rate, 4) for x in link])
    save("4 Saria - matched +2 (light)", [matched(x, rate, 2, 1.06) for x in link])
    save("5 Saria - matched +3 (stronger)", [matched(x, rate, 3, 1.12) for x in link])
    own = ["CallFar00", "CallFar01", "Angry00", "Angry01", "Painful00", "SurpriseL00", "SurpriseM00",
           "SurpriseS01", "Notice00", "LaughS00", "Happy00"]
    save("6 Saria - her own recorded clips",
         [bwav.clip("NV_USen_Custom_Saria_" + n)[0][0].astype(np.float64) for n in own])


if __name__ == "__main__":
    main()
