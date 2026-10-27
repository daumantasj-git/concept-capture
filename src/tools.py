"""Bendri pagalbiniai: konfigūracija, ffmpeg/ffprobe radimas, video metaduomenys."""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent


def _merge(base: dict, over: dict) -> dict:
    for k, v in over.items():
        base[k] = _merge(base.get(k) or {}, v) if isinstance(v, dict) else v
    return base


def load_config() -> dict:
    """config.yaml (bendri parametrai, git'e) + config.local.yaml (šio kompiuterio parametrai, sugeneruoja
    src/machine.py per setup.ps1, į git nepatenka). Vietinis failas perrašo tik tuos raktus, kuriuos turi."""
    cfg = yaml.safe_load((REPO / "config.yaml").read_text(encoding="utf-8"))
    local = REPO / "config.local.yaml"
    if local.exists():
        _merge(cfg, yaml.safe_load(local.read_text(encoding="utf-8")) or {})
    return cfg


def add_cuda_dll_dirs() -> None:
    """NVIDIA bibliotekos (cuBLAS, cuDNN), įdiegtos pip paketais nvidia-*-cu12, guli site-packages/nvidia/*/bin.
    Windows jų pats neranda, todėl prieš importuojant ctranslate2 su CUDA katalogai pridedami ranka."""
    import site
    for sp in site.getsitepackages():
        for bin_dir in glob.glob(os.path.join(sp, "nvidia", "*", "bin")):
            os.add_dll_directory(bin_dir)
            os.environ["PATH"] = bin_dir + os.pathsep + os.environ["PATH"]


def load_vocab() -> list[str]:
    """vocab.txt (bendras, git'e) + asmeninis/vocab.txt (asmeniniai terminai, pvz. darbo sistemų pavadinimai –
    į git nepatenka). Dublikatai išmetami, tvarka išlaikoma."""
    terms: list[str] = []
    for path in (REPO / "vocab.txt", REPO / "asmeninis" / "vocab.txt"):
        if path.exists():
            lines = (l.strip() for l in path.read_text(encoding="utf-8").splitlines())
            terms += [l for l in lines if l and not l.startswith("#")]
    return list(dict.fromkeys(terms))


def find_exe(name: str) -> str:
    """ffmpeg / ffprobe kelias.

    Ne tik PATH: winget ffmpeg įdeda į user PATH, bet procesai, paleisti prieš įdiegimą (pvz. Claude Code sesija),
    seno PATH nemato. Todėl atsarginis variantas – tiesiai winget Gyan.FFmpeg paketo katalogas.
    """
    found = shutil.which(name)
    if found:
        return found
    pattern = os.path.expandvars(rf"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\*\bin\{name}.exe")
    hits = sorted(glob.glob(pattern))
    if hits:
        return hits[-1]
    raise FileNotFoundError(f"{name} nerastas nei PATH'e, nei winget Gyan.FFmpeg kataloge")


def probe(video: Path) -> dict:
    """Trukmė, raiška, fps, kodekas, pasukimas (ffprobe)."""
    out = subprocess.run(
        [find_exe("ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(video)],
        capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    v = next(s for s in data["streams"] if s["codec_type"] == "video")
    rotation = 0
    for sd in v.get("side_data_list", []):
        if "rotation" in sd:
            rotation = int(sd["rotation"])
    num, den = (int(x) for x in v["r_frame_rate"].split("/"))
    return {
        "duration": float(data["format"]["duration"]),
        "width": v["width"], "height": v["height"],
        "fps": num / den if den else 0.0,
        "codec": v["codec_name"],
        "rotation": rotation,
        "has_audio": any(s["codec_type"] == "audio" for s in data["streams"]),
        # telefono įrašymo laikas (UTC), jei telefonas jį įrašė – concept.md „recorded“
        "creation_time": data["format"].get("tags", {}).get("creation_time"),
    }


def imwrite(path: Path, img, quality: int = 90) -> None:
    """cv2.imwrite pakaitalas. Windows'e OpenCV nemoka ne-ASCII kelių (projekto kelyje yra „ė“) ir TYLIAI
    grąžina False – failas tiesiog neatsiranda. imencode + numpy.tofile veikia su bet kokiu keliu."""
    import cv2
    ok, buf = cv2.imencode(Path(path).suffix or ".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise IOError(f"Nepavyko užkoduoti {path}")
    buf.tofile(str(path))


def imread(path: Path):
    """cv2.imread pakaitalas ne-ASCII keliams (žr. imwrite). None, jei failo nėra."""
    import cv2
    import numpy as np
    if not Path(path).exists():
        return None
    return cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)


def fmt_time(t: float) -> str:
    t = max(0.0, t)
    return f"{int(t // 60)}:{int(t % 60):02d}"
