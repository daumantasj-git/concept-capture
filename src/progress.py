"""Apdorojimo progresas -> sessions/videoNNN/work/progress.json.

Failą skaito serveris (serveris/server.js, puslapis /eiga), todėl eigą galima stebėti
naršyklėje kompiuteryje arba telefone. Rašoma atomiškai (tmp + os.replace), kad serveris
niekada neperskaitytų pusiau įrašyto JSON.

Naudojimas pipeline'e:
    p = Progress.new_run(session_dir)
    p.start("whisper", total=duration_s, unit="s")
    p.update("whisper", done=segment.end, note="...")
    p.finish("whisper")
    p.complete()          # arba p.fail("klaida")

Whisper ir kadrų analizė vyksta lygiagrečiai tame pačiame procese (gijos),
todėl visi įrašai eina per vieną užraktą (lock).

CLI – žingsniams, kuriuos daro ne Python, o Claude (analizės subagentas):
    python src/progress.py sessions\\video001 start analysis
    python src/progress.py sessions\\video001 finish analysis
    python src/progress.py sessions\\video001 fail "priežastis"
    python src/progress.py sessions\\video001 complete

Svarbu: Progress objektas laiko būseną atmintyje ir rašydamas perrašo visą failą.
Jei tarp jo įrašų progress.json pakeitė CLI, objektą perkrauk per Progress.load(),
kitaip CLI pakeitimai bus prarasti.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

# (raktas, pavadinimas, svoris). Svoris = kokią dalį viso laiko žingsnis užima.
# Įvertinimas iš CLAUDE.md laikų (whisper turbo ~10–20 min / 15 min įrašui). Kalibruoti pagal
# realius paleidimus: faktinės trukmės lieka progress.json (started/finished), tada perkelti į config.yaml.
PHASES = [
    ("meta", "Video informacija (ffprobe)", 1),
    ("audio", "Garso ištraukimas (ffmpeg)", 2),
    ("whisper", "Transkripcija (whisper)", 55),
    ("frames", "Kadrų analizė", 30),
    ("align", "Piešinio ir žodžių susiejimas", 1),
    ("evidence", "evidence.md surinkimas", 1),
    ("analysis", "AI analizė (subagentas)", 8),
    ("export", "Export: concept.md + pages/", 2),
]

WRITE_INTERVAL_S = 1.0  # update() rašo į diską ne dažniau nei kas sekundę


class Progress:
    def __init__(self, session_dir: Path, state: dict):
        self.session_dir = Path(session_dir)
        self.path = self.session_dir / "work" / "progress.json"
        self.state = state
        self._lock = threading.Lock()
        self._last_write = 0.0

    # ---------- sukūrimas ----------

    @classmethod
    def new_run(cls, session_dir: Path) -> "Progress":
        """Naujas paleidimas: visi žingsniai 'pending'."""
        now = time.time()
        state = {
            "session": Path(session_dir).name,
            "status": "running",
            "started": now,
            "updated": now,
            "finished": None,
            "error": None,
            "phases": [
                {"key": k, "label": label, "weight": w, "status": "pending",
                 "done": 0, "total": None, "unit": None, "note": "",
                 "started": None, "finished": None, "pid": None}
                for k, label, w in PHASES
            ],
        }
        p = cls(session_dir, state)
        p._write(force=True)
        return p

    @classmethod
    def load(cls, session_dir: Path) -> "Progress":
        """Esamas progress.json (CLI atvejui). Jei jo nėra – pradedamas naujas."""
        path = Path(session_dir) / "work" / "progress.json"
        if not path.exists():
            return cls.new_run(session_dir)
        return cls(session_dir, json.loads(path.read_text(encoding="utf-8")))

    # ---------- žingsniai ----------

    def _phase(self, key: str) -> dict:
        for ph in self.state["phases"]:
            if ph["key"] == key:
                return ph
        raise KeyError(f"Nežinomas žingsnis: {key}")

    def start(self, key: str, total: float | None = None, unit: str | None = None,
              note: str = "", external: bool = False) -> None:
        """external=True – žingsnį vykdo ne šis procesas (pvz. Claude), todėl pid nesaugomas
        ir serveris netikrina, ar procesas gyvas."""
        with self._lock:
            ph = self._phase(key)
            ph.update(status="running", done=0, total=total, unit=unit, note=note,
                      started=time.time(), finished=None,
                      pid=None if external else os.getpid())
            self.state["status"] = "running"
            self._write(force=True)

    def update(self, key: str, done: float, total: float | None = None, note: str | None = None,
               unit: str | None = None) -> None:
        """total/unit galima nurodyti ir vėliau nei start() – pvz. whisper trukmę sužino tik pradėjęs."""
        with self._lock:
            ph = self._phase(key)
            ph["done"] = done
            if total is not None:
                ph["total"] = total
            if unit is not None:
                ph["unit"] = unit
            if note is not None:
                ph["note"] = note
            self._write()

    def finish(self, key: str, note: str | None = None) -> None:
        with self._lock:
            ph = self._phase(key)
            ph["status"] = "done"
            ph["finished"] = time.time()
            if ph["total"] is not None:
                ph["done"] = ph["total"]
            if note is not None:
                ph["note"] = note
            self._write(force=True)

    def skip(self, key: str, note: str = "iš kešo (work/)") -> None:
        """Žingsnis nevykdytas, nes rezultatas jau yra (pvz. transcript.json iš ankstesnio paleidimo)."""
        with self._lock:
            ph = self._phase(key)
            ph.update(status="skipped", note=note, finished=time.time())
            self._write(force=True)

    def wait(self, note: str) -> None:
        """Sąmoningai sustota (pvz. po [3] – autorius peržiūri contact sheet'ą). Ne klaida ir ne pabaiga."""
        with self._lock:
            self.state["status"] = "waiting"
            self.state["error"] = None
            self.state["note"] = note
            self.state["finished"] = time.time()
            self._write(force=True)

    def complete(self) -> None:
        with self._lock:
            self.state["status"] = "done"
            self.state["finished"] = time.time()
            self._write(force=True)

    def fail(self, error: str, key: str | None = None) -> None:
        """Pažymi klaidą. Jei key nenurodytas, klaida priskiriama visiems vykdomiems žingsniams."""
        with self._lock:
            for ph in self.state["phases"]:
                if (key is None and ph["status"] == "running") or ph["key"] == key:
                    ph["status"] = "failed"
                    ph["finished"] = time.time()
            self.state["status"] = "failed"
            self.state["error"] = error
            self.state["finished"] = time.time()
            self._write(force=True)

    # ---------- rašymas ----------

    def _write(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_write < WRITE_INTERVAL_S:
            return
        self.state["updated"] = now
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")
        # Windows: os.replace nepavyksta, jei serveris tą akimirką skaito failą. Bandome kelis kartus.
        for attempt in range(10):
            try:
                os.replace(tmp, self.path)
                break
            except PermissionError:
                time.sleep(0.05 * (attempt + 1))
        self._last_write = now


def _cli(argv: list[str]) -> int:
    # complete argumento nereikia, kitiems veiksmams reikia žingsnio rakto arba klaidos teksto.
    if len(argv) < 2 or argv[1] not in {"start", "finish", "skip", "fail", "complete"} \
            or (argv[1] != "complete" and len(argv) < 3):
        print(__doc__)
        return 2
    session_dir, action = Path(argv[0]), argv[1]
    p = Progress.load(session_dir)
    if action == "start":
        p.start(argv[2], note=" ".join(argv[3:]), external=True)
    elif action == "finish":
        p.finish(argv[2])
    elif action == "skip":
        p.skip(argv[2], " ".join(argv[3:]) or "praleista")
    elif action == "fail":
        p.fail(" ".join(argv[2:]))
    elif action == "complete":
        p.complete()
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
