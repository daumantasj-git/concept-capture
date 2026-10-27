"""Pipeline paleidimas vienai sesijai. Trys etapai, tarp jų – sąmoningos pauzės:

    .venv\\Scripts\\python.exe src\\run.py sessions\\video001                    # [1]–[3], tada autorius peržiūri contact sheet
    .venv\\Scripts\\python.exe src\\run.py sessions\\video001 --stage evidence   # [4] → work/evidence.md, laukia analizės
        [5] – analizės subagentas (Claude), rašo work/analysis.md; eiga: src/progress.py ... start/finish analysis
    .venv\\Scripts\\python.exe src\\run.py sessions\\video001 --stage export     # [6] → concept.md + pages/, → video001_processed

Garsas ([2]) ir kadrai ([3]) vyksta lygiagrečiai (atskira gija whisper'iui).
Kešas: work/audio.wav, work/transcript.json, work/events.json – jei yra, žingsnis praleidžiamas (nebent --force).
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from align import align  # noqa: E402
from audio import extract_audio, transcribe  # noqa: E402
from evidence import build_evidence  # noqa: E402
from export import export, rename_processed  # noqa: E402
from frames import analyze  # noqa: E402
from progress import Progress  # noqa: E402
from tools import fmt_time, load_config, probe  # noqa: E402

VIDEO_NAMES = ["video.mp4", "video.mov", "video.m4v", "video.webm", "video.mkv", "video.3gp"]


def stage_prepare(session: Path, video: Path, work: Path, cfg: dict, force: set[str]) -> int:
    """[1]–[3]."""
    p = Progress.new_run(session)
    t0 = time.time()
    try:
        p.start("meta")
        meta = probe(video)
        (work / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
        p.finish("meta", note=f"{fmt_time(meta['duration'])}, {meta['width']}x{meta['height']}, {meta['fps']:.0f} fps, "
                              f"{meta['codec']}, pasukimas {meta['rotation']}")

        audio_error: list[BaseException] = []

        def audio_job():
            try:
                wav, tr = work / "audio.wav", work / "transcript.json"
                if wav.exists() and "audio" not in force:
                    p.skip("audio", "iš kešo (work/audio.wav)")
                else:
                    p.start("audio")
                    extract_audio(video, wav)
                    p.finish("audio")
                if tr.exists() and not ({"audio", "whisper"} & force):
                    p.skip("whisper", "iš kešo (work/transcript.json)")
                else:
                    transcribe(wav, tr, cfg["audio"], cfg["resources"]["whisper_threads"], p)
            except BaseException as e:  # noqa: BLE001 – perduodam pagrindinei gijai
                audio_error.append(e)
                p.fail(f"garsas: {e}", key="whisper")

        th = threading.Thread(target=audio_job, name="audio", daemon=True)
        if meta["has_audio"]:
            th.start()
        else:
            p.skip("audio", "video be garso")
            p.skip("whisper", "video be garso")

        if (work / "events.json").exists() and "frames" not in force:
            p.skip("frames", "iš kešo (work/events.json)")
            result = json.loads((work / "events.json").read_text(encoding="utf-8"))
        else:
            result = analyze(video, work, cfg, p)

        if th.is_alive():
            th.join()
        if audio_error:
            raise audio_error[0]

        sheets = ", ".join(f"work/{n}" for n in result.get("contact_sheets", []))
        p.wait(f"[1]–[3] baigta per {fmt_time(time.time() - t0)}. Peržiūrėk {sheets}, tada leisk toliau.")
        print(f"Baigta per {fmt_time(time.time() - t0)}. Kontaktiniai lapai: {sheets}")
        return 0
    except BaseException as e:  # noqa: BLE001
        p.fail(f"{type(e).__name__}: {e}")
        traceback.print_exc()
        return 1


def stage_evidence(session: Path, work: Path, cfg: dict) -> int:
    """[4] susiejimas + evidence.md. Po jo – pauzė analizės subagentui ([5])."""
    p = Progress.load(session)
    try:
        p.start("align")
        al = align(work, cfg)
        p.finish("align", note=f"{len(al['blocks'])} blokai iš {al['n_events']} įvykių")
        p.start("evidence")
        out = build_evidence(work, json.loads((work / "meta.json").read_text(encoding="utf-8")))
        p.finish("evidence", note=f"{out.stat().st_size // 1024} KB")
        p.wait("[4] baigta. Laukia AI analizės ([5]).")
        print(f"evidence: {out} ({len(al['blocks'])} blokai)")
        return 0
    except BaseException as e:  # noqa: BLE001
        p.fail(f"{type(e).__name__}: {e}")
        traceback.print_exc()
        return 1


def stage_export(session: Path, work: Path, cfg: dict) -> int:
    """[6] concept.md + pages/, tada pervadinimas į _processed."""
    if not (work / "analysis.md").exists():
        print("Nėra work/analysis.md – pirma [5] analizė.", file=sys.stderr)
        return 2
    p = Progress.load(session)
    try:
        p.start("export")
        out = export(session, json.loads((work / "meta.json").read_text(encoding="utf-8")), cfg)
        p.finish("export", note=f"{out.name} + pages/")
        p.complete()
        final = rename_processed(session)
        print(f"concept.md: {final / 'concept.md'}")
        return 0
    except BaseException as e:  # noqa: BLE001
        p.fail(f"{type(e).__name__}: {e}")
        traceback.print_exc()
        return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Koncepcijos video apdorojimas")
    ap.add_argument("session", type=Path, help="sessions\\videoNNN katalogas")
    ap.add_argument("--stage", choices=["prepare", "evidence", "export"], default="prepare")
    ap.add_argument("--force", default="", help="prepare etapui: audio,whisper,frames – perskaičiuoti nepaisant kešo")
    args = ap.parse_args()

    session = args.session.resolve()
    video = next((session / n for n in VIDEO_NAMES if (session / n).exists()), None)
    if video is None:
        print(f"Nėra video {session} kataloge (gal dar keliasi – video.*.part?)", file=sys.stderr)
        return 2
    cfg = load_config()
    work = session / "work"
    work.mkdir(exist_ok=True)
    if args.stage == "prepare":
        return stage_prepare(session, video, work, cfg, {x.strip() for x in args.force.split(",") if x.strip()})
    if args.stage == "evidence":
        return stage_evidence(session, work, cfg)
    return stage_export(session, work, cfg)


if __name__ == "__main__":
    sys.exit(main())
