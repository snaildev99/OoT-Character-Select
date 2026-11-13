"""Make the public source tree: our code and notes, nothing of the game and nothing
personal, in a folder of its own (the git repository that is pushed).

    venv\\Scripts\\python.exe tools\\publish.py <target folder>

Copied: mods\\*.py, tools\\*.py, README.md as docs\\NOTES.md, publish\\* (the public
README, the licence) to the top and publish\\images\\* (the readme's screenshots, made
by re-encoding, so they carry no metadata) to docs\\images. Not copied: builds, virtual environments, assets
(third-party models), research dumps of game data, the working notes for the sessions,
launchers. In every text the author's first name becomes the public handle and paths
under the Windows profile lose the user name; the result is searched for what must not
be in it and the script fails if anything is found. The target's .git folder is kept.
"""
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HANDLE = "snaildev"
PRIVATE_NAME = "".join(["Br", "ett"])       # spelled apart: this file is published too
FORBIDDEN = [PRIVATE_NAME.lower(), "@gm" + "ail", "sant" + "ema", ".cl" + "aude"]
REQUIREMENTS = ["ainb==0.2.17", "astc-encoder-py==0.1.12", "capstone==5.0.9", "keystone-engine==0.9.2",
                "mmh3==5.3.1", "numpy==2.4.6", "pillow==13.0.0", "pyzstd==0.20.0", "texture2ddecoder==1.0.6",
                "unicorn==2.1.4"]
GITIGNORE = ["__pycache__/", "venv*/", "build*/", "dist/", "assets/", "research/", ".vscode/"]


def public_notes(text):
    """The README without its lines between a "private" and a "/private" HTML comment: what
    other people said in chats, and matters of this PC, are not ours to publish."""
    start, end = "<!-- private -->", "<!-- /private -->"
    assert text.count(start) == text.count(end)
    text = re.sub(re.escape(start) + r".*?" + re.escape(end) + r"\r?\n?", "", text, flags=re.S)
    assert "private -->" not in text
    return text


def scrub(text):
    text = re.sub(r"C:[\\/]+Users[\\/]+%s" % PRIVATE_NAME, "%USERPROFILE%", text)
    return text.replace(PRIVATE_NAME, HANDLE)


def main():
    target = os.path.abspath(sys.argv[1])
    assert not target.startswith(ROOT + os.sep) and target != ROOT
    os.makedirs(target, exist_ok=True)
    for name in os.listdir(target):
        if name != ".git":
            path = os.path.join(target, name)
            shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
    files = {}
    for folder in ("mods", "tools"):
        for name in sorted(os.listdir(os.path.join(ROOT, folder))):
            if name.endswith(".py"):
                files[folder + "/" + name] = os.path.join(ROOT, folder, name)
    files["docs/NOTES.md"] = os.path.join(ROOT, "README.md")
    pictures = {}
    for name in sorted(os.listdir(os.path.join(ROOT, "publish"))):
        path = os.path.join(ROOT, "publish", name)
        if name == "images":                # the readme's screenshots: copied as they are
            for picture in sorted(os.listdir(path)):
                assert picture.lower().endswith((".jpg", ".png")), picture
                pictures["docs/images/" + picture] = os.path.join(path, picture)
        else:
            files[name] = path
    out ={rel: scrub(open(src, encoding="utf-8").read()) for rel, src in files.items()}
    out["docs/NOTES.md"] = public_notes(out["docs/NOTES.md"])
    out["requirements.txt"] = "\n".join(REQUIREMENTS) + "\n"
    out[".gitignore"] = "\n".join(GITIGNORE) + "\n"
    for rel, text in out.items():
        low = text.lower()
        for word in FORBIDDEN:
            assert word not in low, "%s still has %r" % (rel, word)
        path = os.path.join(target, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
    for rel, src in pictures.items():
        path = os.path.join(target, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        shutil.copyfile(src, path)
    print("%d files, %d pictures -> %s" % (len(out), len(pictures), target))


if __name__ == "__main__":
    main()
