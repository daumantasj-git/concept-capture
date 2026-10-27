"""[6] Export: concept.md + pages/ sesijos kataloge, tada katalogas pervadinamas į videoNNN_processed.

concept.md struktūra – CLAUDE.md skyrius „concept.md struktūra“. AI interpretacija imama iš work/analysis.md
(analizės subagento rezultatas): 1–4 skyriai → „AI interpretacija“, 5 skyrius → „Atviri klausimai autoriui“.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

from tools import REPO, fmt_time, load_vocab


def pipeline_version() -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "src", "config.yaml"], cwd=REPO,
                               capture_output=True, text=True).stdout.strip()
        return sha + ("-dirty" if dirty else "") if sha else "nežinoma"
    except OSError:
        return "nežinoma"


def split_analysis(text: str) -> tuple[str, str]:
    """analysis.md → (AI interpretacija = 1–4 skyriai, atviri klausimai = 5 skyrius). Antraštės pažeminamos vienu lygiu."""
    m = re.search(r"^##\s*5\.", text, flags=re.M)
    head, questions = (text[:m.start()], text[m.end():]) if m else (text, "")
    head = re.sub(r"^#\s.*\n", "", head, count=1, flags=re.M)  # agento pavadinimą (# ...) išmetam
    questions = re.sub(r"^[^\n]*\n", "", questions, count=1)    # „Atviri klausimai autoriui“ eilutės likutis
    demote = lambda s: re.sub(r"^(#{2,5})\s", lambda g: "#" + g.group(1) + " ", s, flags=re.M)
    return demote(head).strip(), demote(questions).strip()


def export(session: Path, meta: dict, cfg: dict) -> Path:
    work = session / "work"
    al = json.loads((work / "aligned.json").read_text(encoding="utf-8"))
    tr = json.loads((work / "transcript.json").read_text(encoding="utf-8")) if (work / "transcript.json").exists() else {}
    # utf-8-sig: Windows įrankiai (PowerShell, Notepad) dažnai įrašo BOM, tada „# antraštė“ neatpažįstama
    analysis = (work / "analysis.md").read_text(encoding="utf-8-sig") if (work / "analysis.md").exists() else ""
    info = json.loads((session / "info.json").read_text(encoding="utf-8")) if (session / "info.json").exists() else {}
    interp, questions = split_analysis(analysis)

    pages_dir = session / "pages"
    pages_dir.mkdir(exist_ok=True)
    for pg in al["pages"]:
        src = work / "pages_export" / f"page{pg['page']}_final.jpg"
        if src.exists():
            shutil.copyfile(src, pages_dir / src.name)

    recorded = meta.get("creation_time") or info.get("started") or "nežinoma"
    vocab = ", ".join(load_vocab()) or "—"
    tm = tr.get("model", cfg["audio"]["model"])
    L = [
        "---",
        "type: concept-capture",
        f"source_video: {info.get('original_name') or session.name}",
        f"recorded: {recorded}",
        f"duration: {fmt_time(meta['duration'])}",
        f"pages: {len(al['pages'])}",
        f"whisper_model: {tm} ({tr.get('compute_type', 'int8')}, {tr.get('device', 'cpu')})",
        f"pipeline_version: {pipeline_version()}",
        f"generated: {dt.date.today().isoformat()}",
        "---",
        "# Kas čia",
        "Autoriaus nufilmuotas koncepcijos paaiškinimas: piešiama ant popieriaus ir pasakojama.",
        "EVENT = kas nupiešta (vieta, spalva, laikas) + ką autorius tuo metu kalbėjo.",
        "",
        "## Kaip skaityti",
        "- Koordinatės: px, (0,0) = viršutinis kairys lapo kampas. Lapų vaizdai yra `pages/`.",
        "- Spalvos: fiksuotos legendos nėra. Spalva žymi minties giją (kas nupiešta kartu, tuo pačiu metu), ne objekto tipą. "
        "Iš spalvos reikšmės nespėk.",
        "- Įvykiai: EVENT = minties gijos atkarpa (iš eilės tos pačios spalvos brūkšniai viename lape). Lapų keitimai – „Lapų suvestinėje“.",
        "- Pasitikėjimo žymės: [užfiksuota] [AI interpretacija] [neaišku – klausti autoriaus].",
        "",
        "## Kaip gauta",
        "- Kadrai: lokaliai 4 fps → lapas ištiesinamas, šešėliai išlyginami → rašalas patvirtinamas, kai ~2 s matomas nedengiamas rankos; "
        "pirmo pasirodymo laikas = kada nupiešta.",
        "- Gestų (rodymo pirštu) nėra – autorius rodo labai mažai. Rašiklio galiukas nesekamas.",
        "- Lapų vaizdai ištiesinti, pasukti skaitymui, šešėliai pašalinti; ranka pašalinta (kiekvienam taškui – naujausias nedengtas kadras).",
        "- Gali trūkti: smulkmenų, kurios buvo visą laiką uždengtos; labai blankių pieštuko brūkšnių.",
        "- Garsas: faster-whisper, VAD išmetė tylas. Autorius darė pauzes.",
        "- Žodžių ir piešinio sąsaja apytikslė (langas ±15 s), gali būti klaidinga.",
        f"- Whisper gali iškraipyti terminus ir slengą. Naudotas žodynas: {vocab}.",
        "",
        "## Kaip naudoti kitame projekte",
        "- Pirma perskaityk šį header'į, tada lapų vaizdus `pages/`, tada įvykius.",
        "- Lapų vaizdai yra pirminis šaltinis. Jei tekstas su vaizdu prieštarauja, vadovaukis vaizdu ir pažymėk neatitikimą.",
        "- AI interpretacijos nelaikyk autoriaus sprendimu.",
        "",
        "## Lapų suvestinė",
    ]
    for pg in al["pages"]:
        bl = [b for b in al["blocks"] if b["page"] == pg["page"]]
        colors = sorted({b["color"] for b in bl})
        L.append(f"- **Lapas {pg['page']}** – {fmt_time(pg['t_start'])}–{fmt_time(pg['t_end'])}, `pages/page{pg['page']}_final.jpg`, "
                 f"EVENT {bl[0]['id']:02d}–{bl[-1]['id']:02d} ({len(bl)}), spalvos: {', '.join(colors)}" if bl else
                 f"- **Lapas {pg['page']}** – {fmt_time(pg['t_start'])}–{fmt_time(pg['t_end'])}, `pages/page{pg['page']}_final.jpg`")
    L += ["", f"## Įvykiai (EVENT 01…{len(al['blocks']):02d})"]
    for b in al["blocks"]:
        sp = b["speech"]
        L += ["", f"### EVENT {b['id']:02d} · lapas {b['page']} · {b['time']} · {b['color']} (gija {b['thread']})",
              f"- [užfiksuota] Vieta: bbox {b['bbox']} lape `pages/page{b['page']}_final.jpg`",
              f"- [užfiksuota] Kalba [{fmt_time(sp['window'][0])}–{fmt_time(sp['window'][1])}]: "
              f"…{sp['before']} ⟦piešiant: {sp['during'] or '—'}⟧ {sp['after']}…"]
    L += ["", "## Pilna transkripcija (su timestamp)"]
    L += [f"- [{fmt_time(s['start'])}] {s['text']}" for s in tr.get("segments", [])]
    L += ["", "## AI interpretacija", "", interp or "_Analizė dar neatlikta._",
          "", "## Atviri klausimai autoriui", "", questions or "_—_", ""]
    out = session / "concept.md"
    out.write_text("\n".join(L), encoding="utf-8")
    return out


def rename_processed(session: Path) -> Path:
    """videoNNN → videoNNN_processed. Windows neleidžia pervadinti, jei kuris nors failas atidarytas
    (Explorer peržiūra, grotuvas, serveris tą akimirką skaito progress.json) – keli bandymai, tada klaida."""
    if session.name.endswith("_processed") or "_" in session.name:
        return session  # jau apdorotas arba pagalbinis (video001_calib) – nepervadinamas
    target = session.with_name(session.name + "_processed")
    for attempt in range(8):
        try:
            session.rename(target)
            return target
        except PermissionError:
            time.sleep(0.5 * (attempt + 1))
    raise PermissionError(f"Nepavyko pervadinti {session.name} → {target.name}: kažkas laiko atidarytą failą "
                          f"(Explorer, grotuvas?). Uždaryk ir paleisk export dar kartą.")
