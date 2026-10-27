"""[4] Susiejimas: rašalo įvykiai → blokai (minties gijų atkarpos) + ką autorius tuo metu kalbėjo → work/aligned.json.

Kodėl blokai, o ne kiekvienas įvykis: kadrų analizė skaido smulkiai (video001 – 131 įvykis), o analizei svarbi
minties gija. Blokas = iš eilės einantys tos pačios gijos (spalvos) įvykiai tame pačiame lape, tarp kurių
pertrauka ≤ block_gap_s, ne ilgesni nei block_max_s.

Kalbos langas: [t_start − speech_before_s, t_end + speech_after_s] (CLAUDE.md: autorius dažnai pirma pasako, tada
piešia; daro pauzes). Langai gali persidengti – tai normalu, prie sekundžių neprisirišinėjam.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2

from tools import fmt_time, imread, imwrite


def build_blocks(events: list[dict], ac: dict) -> list[dict]:
    ink = sorted((e for e in events if e["type"] == "ink" and not e.get("preexisting")), key=lambda e: e["t_start"])
    blocks: list[dict] = []
    cur = None
    for e in ink:
        same = (cur is not None and e["page"] == cur["page"] and e["thread"] == cur["thread"]
                and e["t_start"] - cur["t_end"] <= ac["block_gap_s"]
                and e["t_end"] - cur["t_start"] <= ac["block_max_s"])
        if same:
            cur["events"].append(e["id"])
            cur["t_end"] = max(cur["t_end"], e["t_end"])
            cur["ink_px"] += e["ink_px"]
            b = cur["bbox"]
            cur["bbox"] = [min(b[0], e["bbox"][0]), min(b[1], e["bbox"][1]), max(b[2], e["bbox"][2]), max(b[3], e["bbox"][3])]
        else:
            cur = {"page": e["page"], "thread": e["thread"], "color": e["color"], "t_start": e["t_start"],
                   "t_end": e["t_end"], "bbox": list(e["bbox"]), "ink_px": e["ink_px"], "events": [e["id"]]}
            blocks.append(cur)
    # Labai maži blokai – dažniausiai triukšmas (šešėlio ar rašiklio likutis), ne piešinys.
    blocks = [b for b in blocks if b["ink_px"] >= ac["block_min_px"]]
    for i, b in enumerate(blocks, 1):
        b["id"] = i
    return blocks


def _words(transcript: dict) -> list[tuple[float, float, str]]:
    out = []
    for seg in transcript.get("segments", []):
        if seg.get("words"):
            out += [(w["start"], w["end"], w["word"]) for w in seg["words"]]
        else:  # be žodžių laikų – visas segmentas kaip vienas „žodis“
            out.append((seg["start"], seg["end"], " " + seg["text"]))
    return out


def speech_for(words, t0: float, t1: float, before: float, after: float) -> dict:
    """Kalba trimis dalimis: prieš piešiant, piešiant, po – kad analizė matytų, kas sakyta tuo pačiu metu."""
    def text(a, b):
        return "".join(w for s, _, w in words if a <= s < b).strip()
    return {"window": [round(max(0.0, t0 - before), 1), round(t1 + after, 1)],
            "before": text(t0 - before, t0), "during": text(t0, t1 + 0.5), "after": text(t1 + 0.5, t1 + after)}


def write_block_images(work: Path, blocks: list[dict], page_long: int, ec: dict) -> None:
    """Bloko crop'as (su paraštėmis) ir lapo miniatiūra su pažymėtu bloku – crop be konteksto neturi prasmės."""
    out = work / "blocks"
    out.mkdir(exist_ok=True)
    pages = {}
    for b in blocks:
        if b["page"] not in pages:
            pages[b["page"]] = imread(work / "pages_full" / f"page{b['page']}_final.jpg")
        img = pages[b["page"]]
        if img is None:
            continue
        s = max(img.shape[:2]) / page_long  # bbox – export (pages/*.jpg) koordinatės
        x0, y0, x1, y1 = b["bbox"]
        mx, my = max(10, (x1 - x0) * 0.15), max(10, (y1 - y0) * 0.15)
        fx0, fy0 = int(max(0, (x0 - mx) * s)), int(max(0, (y0 - my) * s))
        fx1, fy1 = int(min(img.shape[1], (x1 + mx) * s)), int(min(img.shape[0], (y1 + my) * s))
        crop = img[fy0:fy1, fx0:fx1]
        k = ec["crop_max_side"] / max(crop.shape[:2])
        if k < 1:
            crop = cv2.resize(crop, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
        b["crop"] = f"blocks/b{b['id']:02d}.jpg"
        imwrite(work / b["crop"], crop, 90)

        k = ec["thumb_long_side"] / max(img.shape[:2])
        thumb = cv2.resize(img, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
        t = k * s
        cv2.rectangle(thumb, (int(x0 * t), int(y0 * t)), (int(x1 * t), int(y1 * t)), (255, 0, 255), 2)
        b["thumb"] = f"blocks/b{b['id']:02d}_thumb.jpg"
        imwrite(work / b["thumb"], thumb, 85)


def align(work: Path, cfg: dict) -> dict:
    ac, ec = cfg["align"], cfg["export"]
    ev = json.loads((work / "events.json").read_text(encoding="utf-8"))
    tr = json.loads((work / "transcript.json").read_text(encoding="utf-8")) if (work / "transcript.json").exists() else {}
    blocks = build_blocks(ev["events"], ac)
    words = _words(tr)
    for b in blocks:
        b["speech"] = speech_for(words, b["t_start"], b["t_end"], ac["speech_before_s"], ac["speech_after_s"])
        b["time"] = f"{fmt_time(b['t_start'])}–{fmt_time(b['t_end'])}"
    write_block_images(work, blocks, ec["page_long_side"], ec)

    # Lapų vaizdai export dydžio (≤800 px) – juos mato analizės agentas ir jie keliauja į pages/.
    (work / "pages_export").mkdir(exist_ok=True)
    for pg in ev["pages"]:
        img = imread(work / "pages_full" / f"page{pg['page']}_final.jpg")
        if img is None:
            continue
        k = ec["page_long_side"] / max(img.shape[:2])
        imwrite(work / "pages_export" / f"page{pg['page']}_final.jpg",
                cv2.resize(img, None, fx=k, fy=k, interpolation=cv2.INTER_AREA), 90)

    result = {"pages": ev["pages"], "blocks": blocks, "n_events": sum(1 for e in ev["events"] if e["type"] == "ink"),
              "n_preexisting": sum(1 for e in ev["events"] if e.get("preexisting"))}
    (work / "aligned.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result
