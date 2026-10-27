"""evidence.md: viskas, ką gauna analizės subagentas ([5]) – lapai, blokai su kalba, pilna transkripcija.

Rašoma taip, kad agentas be jokio kito konteksto suprastų, kaip medžiaga gauta ir ko ja negalima klausti.
Vaizdų keliai – santykiniai su work/ katalogu.
"""

from __future__ import annotations

import json
from pathlib import Path

from tools import fmt_time


def build_evidence(work: Path, meta: dict) -> Path:
    al = json.loads((work / "aligned.json").read_text(encoding="utf-8"))
    tr = json.loads((work / "transcript.json").read_text(encoding="utf-8")) if (work / "transcript.json").exists() else {}
    L = []
    L.append("# Evidence: koncepcijos video")
    L.append("")
    L.append(f"Video {fmt_time(meta['duration'])}. Autorius piešia koncepciją ant popieriaus lapų ir kartu ją pasakoja lietuviškai.")
    L.append("")
    L.append("## Kaip gauta (svarbu interpretuojant)")
    L.append("- Kas ir kada nupiešta – iš atsiradusio rašalo (lokali kadrų analizė). Rašiklio galiukas ir gestai NEsekami.")
    L.append("- BLOCK = iš eilės einantys tos pačios spalvos brūkšniai tame pačiame lape (viena minties gija).")
    L.append("- **Spalvos fiksuotos reikšmės NETURI.** Spalva žymi tik tai, kas nupiešta kartu, tuo pačiu metu (minties gija). "
             "Iš spalvos reikšmės nespėk: rodyklės ir ryšiai būna bet kokios spalvos.")
    L.append("- Kalba: faster-whisper (lietuvių k.). Gali iškraipyti terminus ir slengą (pvz. „seivinti“, „mergint“) – slengo netaisyk. "
             "Autorius daro pauzes, dažnai pirma pasako, tada piešia.")
    L.append("- Kalbos ir piešinio sąsaja apytikslė: kiekvienam blokui – kalba prieš / piešiant / po (±15 s).")
    L.append("- Lapų vaizdai yra pirminis šaltinis. Jei tekstas su vaizdu prieštarauja – vadovaukis vaizdu ir pažymėk neatitikimą.")
    L.append("- Koordinatės: px lapo vaizde (0,0 = viršutinis kairys kampas).")
    L.append("")
    L.append("## Lapai")
    for pg in al["pages"]:
        n = sum(1 for b in al["blocks"] if b["page"] == pg["page"])
        L.append(f"- Lapas {pg['page']}: {fmt_time(pg['t_start'])}–{fmt_time(pg['t_end'])}, blokų {n}. Vaizdas: `pages_export/page{pg['page']}_final.jpg`")
    L.append("")
    L.append(f"## Blokai ({len(al['blocks'])}; iš {al['n_events']} smulkių įvykių)")
    for b in al["blocks"]:
        sp = b["speech"]
        L.append("")
        L.append(f"### BLOCK {b['id']:02d} · lapas {b['page']} · {b['time']} · {b['color']} · gija {b['thread']}")
        L.append(f"- bbox {b['bbox']} · crop `{b.get('crop', '—')}` · lapo miniatiūra `{b.get('thumb', '—')}`")
        L.append(f"- Kalba [{fmt_time(sp['window'][0])}–{fmt_time(sp['window'][1])}]: "
                 f"…{sp['before']} ⟦piešiant: {sp['during'] or '—'}⟧ {sp['after']}…")
    L.append("")
    L.append("## Pilna transkripcija")
    for seg in tr.get("segments", []):
        L.append(f"- [{fmt_time(seg['start'])}] {seg['text']}")
    out = work / "evidence.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    return out
