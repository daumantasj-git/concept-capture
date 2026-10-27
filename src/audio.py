"""[2] Garsas: video → audio.wav (16 kHz mono) → faster-whisper → transcript.json.

Slengas („seivinti“, „mergint“) netaisomas – transkripcija išsaugoma tokia, kokią davė whisper.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from progress import Progress
from tools import add_cuda_dll_dirs, find_exe, load_vocab


def extract_audio(video: Path, wav: Path) -> None:
    wav.parent.mkdir(parents=True, exist_ok=True)
    tmp = wav.with_suffix(".tmp.wav")
    subprocess.run([find_exe("ffmpeg"), "-v", "error", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
                    "-c:a", "pcm_s16le", str(tmp)], check=True)
    tmp.replace(wav)  # pusiau įrašytas wav niekada nepatenka į kešą


def load_wav(wav: Path) -> np.ndarray:
    """16 kHz mono PCM16 wav → float32 [-1, 1].

    Kodėl ne faster-whisper'iui failo kelias: tada jis dekoduoja per PyAV, o faster-whisper 1.2.1 su PyAV 19
    nesuderinami (av.open() nebepriima metadata_errors → TypeError). Masyvą paduodant PyAV nenaudojamas visai,
    ir garsas nededamas antrą kartą (ffmpeg jau paruošė būtent tą formatą, kurio reikia whisper'iui).
    """
    with wave.open(str(wav), "rb") as w:
        if w.getframerate() != 16000 or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError(f"{wav}: tikėtasi 16 kHz mono PCM16, gauta {w.getframerate()} Hz, "
                             f"{w.getnchannels()} kan., {w.getsampwidth() * 8} bit")
        data = w.readframes(w.getnframes())
    return np.frombuffer(data, np.int16).astype(np.float32) / 32768.0


SR = 16000


def quiet_cuts(audio: np.ndarray, parts: int, search_s: float = 8.0) -> list[int]:
    """Pjūvių vietos (mėginiais) dalinant garsą į parts dalių: prie kiekvieno idealaus taško ±search_s ieškoma
    tyliausio 0,5 s lango, kad pjūvis nekirstų žodžio. Grąžina [0, pjūvis1, …, len(audio)]."""
    win = SR // 2
    cuts = [0]
    for k in range(1, parts):
        center = len(audio) * k // parts
        lo, hi = max(0, center - int(search_s * SR)), min(len(audio) - win, center + int(search_s * SR))
        energy = [(float(np.mean(audio[i:i + win] ** 2)), i) for i in range(lo, hi, SR // 10)]
        cuts.append(min(energy)[1] + win // 2)
    return cuts + [len(audio)]


def transcribe(wav: Path, out_json: Path, cfg: dict, threads: int, progress: Progress) -> dict:
    """Transkripcija. Garsas dalinamas į cfg['parallel_parts'] dalių tyliose vietose, ir dalys transkribuojamos
    lygiagrečiai: vienas modelis su num_workers=dalių, kiekvienas worker'is – `threads` gijų.

    Kodėl dalinti: vienas whisper procesas blogai išnaudoja papildomas gijas (video001_calib, 120 s, vienas:
    4 gijos 85 s, 8 gijos 72 s – tik −15%), o 2 dalys × 4 gijos – 54 s. Kokybė nepakito (žr. config.yaml).
    """
    device = cfg.get("device", "cpu")
    if device == "cuda":
        add_cuda_dll_dirs()
    from faster_whisper import WhisperModel  # importas lėtas, todėl tik čia

    vocab = load_vocab()
    # hotwords perduodami kiekvienam ~30 s langui; initial_prompt – tik pirmam (kai condition_on_previous_text=false).
    hotwords = ", ".join(vocab) if vocab else None
    prompt = ("Pokalbis lietuviškai apie duomenų sistemas. Terminai: " + ", ".join(vocab) + ".") if vocab else None
    parts = max(1, int(cfg.get("parallel_parts", 1)))

    progress.start("whisper", note=f"kraunamas modelis {cfg['model']}…")
    t0 = time.time()
    audio = load_wav(wav)
    duration = len(audio) / SR
    cuts = quiet_cuts(audio, parts)
    model = WhisperModel(cfg["model"], device=device, compute_type=cfg["compute_type"],
                         cpu_threads=threads, num_workers=parts)
    progress.update("whisper", done=0, total=round(duration, 1), unit="s", note=f"transkribuojama ({parts} dalys)…")

    done_per_part = [0.0] * parts
    lock = threading.Lock()

    def run_part(i: int) -> tuple[list[dict], float]:
        offset = cuts[i] / SR
        segments, info = model.transcribe(
            audio[cuts[i]:cuts[i + 1]], language=cfg["language"], beam_size=cfg["beam_size"],
            vad_filter=cfg["vad_filter"], word_timestamps=True, initial_prompt=prompt, hotwords=hotwords,
            condition_on_previous_text=cfg["condition_on_previous_text"])
        out = []
        # segments – generatorius: transkripcija vyksta iteruojant. Laikai – dalies viduje, todėl + offset.
        for seg in segments:
            out.append({
                "start": round(seg.start + offset, 2), "end": round(seg.end + offset, 2), "text": seg.text.strip(),
                "avg_logprob": round(seg.avg_logprob, 3), "no_speech_prob": round(seg.no_speech_prob, 3),
                "words": [{"start": round(w.start + offset, 2), "end": round(w.end + offset, 2), "word": w.word,
                           "prob": round(w.probability, 3)} for w in (seg.words or [])],
            })
            with lock:
                done_per_part[i] = seg.end
                progress.update("whisper", done=round(min(sum(done_per_part), duration), 1),
                                note=seg.text.strip()[:120])
        return out, getattr(info, "duration_after_vad", info.duration)

    with ThreadPoolExecutor(max_workers=parts) as ex:
        results = list(ex.map(run_part, range(parts)))
    out = sorted((s for segs, _ in results for s in segs), key=lambda s: s["start"])
    for i, s in enumerate(out):
        s["id"] = i

    result = {
        "model": cfg["model"], "device": device, "compute_type": cfg["compute_type"], "cpu_threads": threads,
        "parallel_parts": parts,
        "cuts_s": [round(c / SR, 2) for c in cuts[1:-1]],
        "language": cfg["language"], "duration": round(duration, 2),
        "duration_after_vad": round(sum(d for _, d in results), 2),
        "elapsed_s": round(time.time() - t0, 1), "vocab": vocab, "segments": out,
    }
    tmp = out_json.with_suffix(".tmp.json")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(out_json)
    rtf = result["elapsed_s"] / max(duration, 1)
    progress.finish("whisper", note=f"{len(out)} segmentų, {result['elapsed_s']:.0f} s ({rtf:.2f}× įrašo trukmės)")
    return result
