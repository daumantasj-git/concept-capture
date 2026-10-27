"""Šio kompiuterio įvertinimas → config.local.yaml (paleidžia setup.ps1, galima ir ranka):

    .venv\\Scripts\\python.exe src\\machine.py            # aptikti, parsiųsti whisper modelį, įrašyti config.local.yaml
    .venv\\Scripts\\python.exe src\\machine.py --no-model # be modelio parsiuntimo (CUDA tada netikrinama)

Ką tikrina: CPU (fiziniai branduoliai), RAM, diską, GPU, ar veikia CUDA (whisper'iui) ir ar OpenCV moka
dekoduoti video GPU. Pagal tai parenka gijų skaičių ir kiek dalių garsą transkribuoti lygiagrečiai.

Taisyklės išvestos iš matavimų Ryzen 7 4700U (8 branduoliai, žr. config.yaml resources). Kitiems kompiuteriams
jos yra įvertinimas: po pirmo įrašo verta pamatuoti (pvz. paprašyti Claude perbenchmarkinti trumpą ištrauką).
"""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tools import REPO, add_cuda_dll_dirs, find_exe, load_config  # noqa: E402


def _ps(command: str) -> str:
    """Trumpa PowerShell užklausa (CIM) – Python'e nėra paprasto būdo gauti fizinių branduolių ar GPU vardų."""
    out = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True)
    return out.stdout.strip()


def cpu_info() -> dict:
    name = _ps("(Get-CimInstance Win32_Processor | Select-Object -First 1).Name") or "nežinomas"
    cores = _ps("(Get-CimInstance Win32_Processor | Measure-Object NumberOfCores -Sum).Sum")
    logical = os.cpu_count() or 1
    return {"name": name, "cores": int(cores) if cores.isdigit() else logical, "logical": logical}


def ram_gb() -> float:
    class MemStatus(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = MemStatus()
    m.dwLength = ctypes.sizeof(MemStatus)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return round(m.ullTotalPhys / 2**30, 1)


def gpus() -> list[str]:
    names = _ps("(Get-CimInstance Win32_VideoController).Name")
    return [n.strip() for n in names.splitlines() if n.strip()]


def cuda_works(model: str) -> tuple[bool, str]:
    """Ar whisper tikrai veikia su CUDA: ne tik ar matomas įrenginys, bet ir ar užsikrauna modelis ir
    transkribuoja sekundę tylos (be cuDNN/cuBLAS bibliotekų lūžta tik čia)."""
    add_cuda_dll_dirs()
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() == 0:
            return False, "CUDA įrenginių nėra"
        import numpy as np
        from faster_whisper import WhisperModel
        m = WhisperModel(model, device="cuda", compute_type="float16")
        list(m.transcribe(np.zeros(16000, np.float32), language="lt")[0])
        return True, "veikia"
    except Exception as e:  # noqa: BLE001 – bet kokia klaida = CUDA nenaudojam
        return False, f"{type(e).__name__}: {str(e)[:150]}"


def hw_decode_works() -> bool:
    """Sugeneruojamas 2 s H.264 video (kaip iš telefono, vertikalus) ir bandoma jį atidaryti su GPU dekodavimu."""
    import cv2
    with tempfile.TemporaryDirectory() as tmp:
        clip = Path(tmp) / "test.mp4"
        subprocess.run([find_exe("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=30",
                        "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)], check=True)
        cap = cv2.VideoCapture(str(clip), cv2.CAP_FFMPEG, [cv2.CAP_PROP_HW_ACCELERATION, cv2.VIDEO_ACCELERATION_ANY])
        ok = cap.isOpened() and cap.read()[0] and cap.get(cv2.CAP_PROP_HW_ACCELERATION) > 0
        cap.release()
        return bool(ok)


def choose(cores: int, ram: float, cuda: bool) -> dict:
    """Parametrai pagal branduolius / RAM / CUDA.

    Kadrams (frame_workers) daugiau nei 3 gijų neapsimoka: nuo 4 procesų greitis beveik nebeauga (matuota).
    Whisper'iui lieka branduoliai − 2 (kadrai + sistema). Vienas whisper blogai išnaudoja daug gijų, o dalys po
    ~3 gijas – gerai: 8 branduoliuose 2 × 3 buvo greičiausia (4 × 2 – lėčiau). Kiekviena dalis – atskira modelio
    kopija atmintyje, todėl mažai RAM → mažiau dalių (įvertinimas).
    """
    frame_workers = 3 if cores >= 8 else 2 if cores >= 4 else 1
    if cuda:
        # GPU transkribuoja daug greičiau nei kadrai analizuojami – dalinti nereikia.
        return {"frame_workers": frame_workers, "whisper_threads": 4, "parallel_parts": 1,
                "device": "cuda", "compute_type": "float16"}
    total = max(2, cores - 2)
    parts = max(1, min(4, total // 3))
    if ram < 8:
        parts = 1
    elif ram < 12:
        parts = min(parts, 2)
    return {"frame_workers": frame_workers, "whisper_threads": max(1, total // parts), "parallel_parts": parts,
            "device": "cpu", "compute_type": "int8"}


def prefetch_model(model: str) -> None:
    """Whisper modelis (~1,6 GB) parsiunčiamas į HuggingFace cache dabar, o ne per pirmą įrašą."""
    from faster_whisper import WhisperModel
    WhisperModel(model, device="cpu", compute_type="int8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Kompiuterio įvertinimas → config.local.yaml")
    ap.add_argument("--no-model", action="store_true", help="neparsiųsti whisper modelio (CUDA netikrinama)")
    args = ap.parse_args()
    # Kai išvestis nukreipta (ne į konsolę), Windows Python rašo cp1252 ir lūžta ties „č“, „ė“.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    model = load_config()["audio"]["model"]

    print("Tikrinamas kompiuteris…")
    cpu, ram, gpu = cpu_info(), ram_gb(), gpus()
    disk = round(shutil.disk_usage(REPO).free / 2**30, 1)
    print(f"  CPU: {cpu['name']} – {cpu['cores']} branduoliai / {cpu['logical']} gijos")
    print(f"  RAM: {ram} GB, laisva diske: {disk} GB")
    print(f"  GPU: {', '.join(gpu) or 'nerasta'}")

    if not args.no_model:
        print(f"Parsiunčiamas whisper modelis {model} (jei dar nėra, ~1,6 GB)…")
        prefetch_model(model)

    cuda, cuda_note = False, "netikrinta (--no-model)"
    if not args.no_model:
        if any("nvidia" in g.lower() for g in gpu):
            cuda, cuda_note = cuda_works(model)
        else:
            cuda_note = "NVIDIA GPU nėra"
    print(f"  CUDA whisper'iui: {'taip' if cuda else 'ne'} ({cuda_note})")
    hw = hw_decode_works()
    print(f"  GPU video dekodavimas: {'veikia' if hw else 'neveikia, bus CPU'}")

    c = choose(cpu["cores"], ram, cuda)
    local = {
        "resources": {"whisper_threads": c["whisper_threads"], "frame_workers": c["frame_workers"], "hw_decode": hw},
        "audio": {"device": c["device"], "compute_type": c["compute_type"], "parallel_parts": c["parallel_parts"]},
    }
    header = (
        f"# Šio kompiuterio parametrai – perrašo config.yaml. Sugeneravo src/machine.py {dt.date.today()}.\n"
        f"# Į git nepatenka. Pakeitei kompiuterį ar aparatūrą – perleisk setup.ps1 (arba src/machine.py).\n"
        f"#\n"
        f"# CPU: {cpu['name']} – {cpu['cores']} branduoliai / {cpu['logical']} gijos\n"
        f"# RAM: {ram} GB, laisva diske: {disk} GB\n"
        f"# GPU: {', '.join(gpu) or 'nerasta'}\n"
        f"# CUDA whisper'iui: {'taip' if cuda else 'ne'} ({cuda_note})\n"
        f"# GPU video dekodavimas: {'veikia' if hw else 'neveikia'}\n"
        f"# Parinkta taisyklėmis iš src/machine.py choose() (įvertinimas, ne matavimas šiame kompiuteryje).\n\n"
    )
    out = REPO / "config.local.yaml"
    out.write_text(header + yaml.safe_dump(local, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"\nWhisper: {c['device']}, {c['parallel_parts']} dalys × {c['whisper_threads']} gijos; "
          f"kadrai: {c['frame_workers']} gijos. Įrašyta į {out.name}")
    if disk < 10:
        print(f"DĖMESIO: diske tik {disk} GB. Vienas 15 min įrašas su tarpiniais failais užima 2–5 GB.")
    if ram < 8:
        print(f"DĖMESIO: RAM tik {ram} GB – apdorojimas bus lėtas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
