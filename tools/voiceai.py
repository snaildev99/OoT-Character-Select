"""AI voice conversion samples: Link's shouts in an NPC's own voice.

Uses Seed-VC (zero-shot, its 44.1 kHz model that follows the source's pitch),
kept outside our code in `build-voice\\ai\\seed-vc` (GPL-3.0, a tool only; its
models download themselves into its `checkpoints` folder on the first run).
Runs in its own environment:

    venv-voice\\Scripts\\python.exe tools\\voiceai.py            the samples
    venv-voice\\Scripts\\python.exe tools\\voiceai.py --check    how close each
        sample is to the NPC's voice (speaker embedding, numbers only)

The source is the same ten shouts as `voicesamples.py` in one file; the
reference is the NPC's own recorded clips (up to 25 seconds are used).
"""
import glob, os, subprocess, sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bwav
import voicesamples

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEEDVC = os.path.join(ROOT, "build-voice", "ai", "seed-vc")
WORK = os.path.join(ROOT, "build-voice", "ai", "work")
STEPS = 50
# the livelier clips first: they are what the first 25 seconds should hold
ORDER = ["CallFar", "Angry", "SurpriseL", "SurpriseM", "Happy", "LaughL", "Notice", "Painful",
         "SurpriseS", "LaughS", "Greeting", "TalkPositive", "QuestionNormal", "Ease", "Think", "Sad"]


def trimmed(x, rate, level=0.02):
    """Cut the silence before and after a clip."""
    loud = np.nonzero(abs(x) > level * abs(x).max())[0]
    pad = int(0.02 * rate)
    return x[max(0, loud[0] - pad):loud[-1] + pad]


def reference(voice, lang="USen", seconds=25.0, skip=()):
    """The NPC's clips in one row; returns (samples, rate, names used)."""
    prefix = "NV_%s_%s_" % (lang, voice) if voice.startswith("Custom_") else "NV_%s_" % voice
    names = [os.path.basename(p)[:-5] for p in glob.glob(os.path.join(bwav.STREAM, prefix + "*.bwav"))]
    names = [n for n in names if not n[len(prefix):].startswith(("Dm_", "Ev_")) and n not in skip]
    rank = lambda n: next((i for i, e in enumerate(ORDER) if n[len(prefix):].startswith(e)), len(ORDER))
    names.sort(key=lambda n: (rank(n), n))
    parts, used, total, rate = [], [], 0, 48000
    for n in names:
        s, rate = bwav.clip(n)
        x = trimmed(s[0].astype(np.float64), rate)
        if total + len(x) > seconds * rate:
            continue
        parts += [x, np.zeros(int(0.12 * rate))]
        used.append(n)
        total += len(x) + int(0.12 * rate)
    x = np.concatenate(parts)
    return x * (0.9 / abs(x).max()), rate, used


def convert(source, ref, shift, tag):
    """Run Seed-VC once; returns the converted file's path."""
    out = os.path.join(WORK, tag)
    os.makedirs(out, exist_ok=True)
    for old in glob.glob(os.path.join(out, "*.wav")):
        os.remove(old)
    subprocess.run([sys.executable, "inference.py", "--source", source, "--target", ref, "--output", out,
                    "--diffusion-steps", str(STEPS), "--f0-condition", "True", "--auto-f0-adjust", "False",
                    "--semi-tone-shift", str(shift), "--fp16", "True"], cwd=SEEDVC, check=True)
    return glob.glob(os.path.join(out, "*.wav"))[0]


def finish(path, name, length):
    """44.1 kHz float -> 48 kHz, the samples' peak, the source's length."""
    import librosa, soundfile
    x, rate = soundfile.read(path, dtype="float64")
    x = librosa.resample(x, orig_sr=rate, target_sr=48000)
    x = np.pad(x, (0, max(0, length - len(x))))[:length]
    x *= voicesamples.PEAK / abs(x).max()
    bwav.write_wav(os.path.join(voicesamples.OUT, name + ".wav"), x[None, :], 48000)
    print("%-40s %5.1f s" % (name, len(x) / 48000))


def main():
    os.makedirs(WORK, exist_ok=True)
    link = [bwav.clip(a, "Player_Child")[0][0].astype(np.float64) for a in voicesamples.LINK]
    src = voicesamples.joined(link, 48000)
    bwav.write_wav(os.path.join(WORK, "link.wav"), src[None, :], 48000)
    runs = (("Custom_Saria", 0, "7 Saria - AI voice (Link's pitch)"),
            ("Custom_Saria", 2, "8 Saria - AI voice +2"),
            ("Custom_Zelda_Child", 3, "9 Young Zelda - AI voice +3"))
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    for voice, shift, name in runs:
        if only and name.split()[0] not in only:
            continue
        ref, rate, used = reference(voice)
        bwav.write_wav(os.path.join(WORK, voice + ".wav"), ref[None, :], rate)
        print("reference: %d of %s's clips, %.1f s" % (len(used), voice, len(ref) / rate))
        finish(convert(os.path.join(WORK, "link.wav"), os.path.join(WORK, voice + ".wav"), shift,
                       "%s_%d" % (voice, shift)), name, len(src))


def check():
    """Cosine similarity of each sample's speaker embedding to Saria and to Link.
    Saria is measured on clips the conversion never saw (another language's)."""
    import librosa, soundfile, torch, torchaudio
    sys.path.insert(0, SEEDVC)
    from modules.campplus.DTDNN import CAMPPlus
    model = CAMPPlus(feat_dim=80, embedding_size=192)
    model.load_state_dict(torch.load(os.path.join(SEEDVC, "campplus_cn_common.bin"), map_location="cpu"))
    model.eval()

    def embed(x, rate):
        w = torch.tensor(librosa.resample(x, orig_sr=rate, target_sr=16000)).float()[None]
        feat = torchaudio.compliance.kaldi.fbank(w, num_mel_bins=80, dither=0, sample_frequency=16000)
        with torch.no_grad():
            return model((feat - feat.mean(dim=0, keepdim=True))[None])[0]

    cos = lambda a, b: float(torch.nn.functional.cosine_similarity(a, b, dim=0))
    held, rate, used = reference("Custom_Saria", lang="JPja")
    saria = embed(held, rate)
    files = sorted(glob.glob(os.path.join(voicesamples.OUT, "*.wav")))
    link = embed(*soundfile.read(files[0], dtype="float64"))
    print("%-40s %8s %8s" % ("sample", "to Saria", "to Link"))
    for p in files:
        e = embed(*soundfile.read(p, dtype="float64"))
        print("%-40s %8.2f %8.2f" % (os.path.basename(p)[:-4], cos(e, saria), cos(e, link)))


if __name__ == "__main__":
    check() if "--check" in sys.argv else main()
