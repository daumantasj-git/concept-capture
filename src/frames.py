"""[3] Kadrų analizė: video → events.json + galutiniai lapų vaizdai + crop'ai + kontaktinis lapas (debug).

Galiuko nesekame (autoriaus sprendimas), todėl „kas ir kada nupiešta“ nustatoma iš PATVIRTINTO RAŠALO:
  - kiekvienas imties kadras (4 fps) → lapas ištiesinamas → apšvietimas išlyginamas → rašalo kaukė
    (plonos tamsios linijos, black-hat) ir rankos kaukė (oda + aplinkinė zona = „nematoma“);
  - pikselis tampa patvirtintu rašalu, kai confirm_s sekundžių iš eilės matomas kaip rašalas (ranka nedengia).
    Jo pirmo pasirodymo laikas = kada nupiešta. Rankos šešėlis (platus) ir rašiklio korpusas (storas, juda)
    rašalu netampa;
  - nauji patvirtinti pikseliai kaupiami; po event_gap_s be naujo rašalo jie sugrupuojami į įvykius (bbox, laikas,
    spalva);
  - naujas lapas = dingo daugiau nei page_missing_frac matomo patvirtinto rašalo (page_missing_s iš eilės).

Lygiagretumas (B variantas): video dekoduojamas pagrindinėje gijoje (D3D11 per OpenCV), sunkus darbas
(ištiesinimas, išlyginimas, kaukės) – ThreadPoolExecutor gijose; OpenCV/numpy atleidžia GIL.
Būsena (PageState) atnaujinama pagrindinėje gijoje griežtai laiko tvarka.
"""

from __future__ import annotations

import json
import shutil
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from flatten import flatten, median_stack
from page import find_page, hand_fraction, rectify, rectify_matrix, robust_corners, skin_mask
from progress import Progress
from tools import fmt_time, imread, imwrite

COLOR_NAMES = {1: "juoda", 2: "pieštukas", 3: "raudona", 4: "mėlyna", 5: "žalia", 6: "kita"}
# bbox piešimo spalva debug vaizduose (BGR); ne rašalo spalva, kad rėmelis neatrodytų kaip piešinys
DRAW_BGR = {1: (60, 60, 60), 2: (130, 130, 130), 3: (40, 40, 220), 4: (220, 90, 30), 5: (40, 170, 40), 6: (200, 0, 200)}

ANALYSIS_SMALL_W = 540  # D variantas: kadras sumažinamas iki 540x960 prieš visa kita
FINAL_LONG_SIDE = 1600


# ---------------------------------------------------------------- gijose vykdomas darbas

@dataclass
class Sample:
    t: float
    hidden: np.ndarray     # bool: ranka / aplink ranką – šiame kadre nežinome, kas po ja
    ink: np.ndarray        # bool: matomas rašalas
    colors: np.ndarray     # uint8: COLOR_NAMES klasė rašalo pikseliams
    hand_frac: float
    raw_full: np.ndarray | None  # pilnos raiškos kadras (tik kas ~2 s) – galutiniam vaizdui su patikslintais kampais
    corners: np.ndarray          # kuriais (užfiksuotais) kampais ištiesinta – analizės koordinačių pagrindas
    det: np.ndarray | None       # šio kadro kampų aptikimas (kas ~2 s) – kampams patikslinti lapo pabaigoje


def ink_mask(flat: np.ndarray, kernel_px: int, contrast: float) -> np.ndarray:
    """Plonos tamsios linijos išlygintame lape. Tamsiausias kanalas – kad būtų pagautas bet kokios spalvos rašalas."""
    darkest = flat.min(axis=2)
    se = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_px, kernel_px))
    blackhat = cv2.morphologyEx(darkest, cv2.MORPH_BLACKHAT, se)
    local_paper = cv2.morphologyEx(darkest, cv2.MORPH_CLOSE, se).astype(np.float32)
    return blackhat > contrast * np.maximum(local_paper, 1.0)


def classify_colors(flat: np.ndarray, ink: np.ndarray) -> np.ndarray:
    """Rašalo spalvos klasė (COLOR_NAMES). Ribos – įvertinimas, tikrinti contact sheet'e."""
    hsv = cv2.cvtColor(flat, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    out = np.zeros(ink.shape, np.uint8)
    neutral = s < 70
    out[ink & neutral & (v < 110)] = 1
    out[ink & neutral & (v >= 110)] = 2
    col = ink & ~neutral
    red = (h <= 10) | (h >= 160)
    blue = (h >= 95) & (h <= 135)
    green = (h >= 35) & (h <= 90)
    out[col & red] = 3
    out[col & blue] = 4
    out[col & green] = 5
    out[col & ~(red | blue | green)] = 6
    return out


def process_sample(t: float, small: np.ndarray, corners: np.ndarray, full: np.ndarray | None, fc: dict) -> Sample:
    page = rectify(small, corners, long_side=fc["analysis_long_side"])
    skin = skin_mask(page)
    d = fc["hand_dilate_px"] * 2 + 1
    hidden = cv2.dilate(skin, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (d, d))).astype(bool)
    flat = flatten(page)
    ink = ink_mask(flat, fc["ink_kernel_px"], fc["ink_contrast"]) & ~hidden
    # Lapo kraštas: black-hat ten pagauna stalo juostelę (netikslūs kampai). Puslapio numeris („1.“) prasideda ~8 px.
    m = fc["border_px"]
    ink[:m] = ink[-m:] = False
    ink[:, :m] = ink[:, -m:] = False
    det = find_page(small) if full is not None else None
    return Sample(t, hidden, ink, classify_colors(flat, ink), float(skin.mean()), full, corners, det)


def _occluders(rect: np.ndarray, flat: np.ndarray, scale: float) -> np.ndarray:
    """Kas uždengia lapą: oda (ranka) + bet koks STORAS ne baltas objektas (rašiklio korpusas bet kokios spalvos).

    Storas = išlieka po morfologinio open su branduoliu, platesniu nei rašalo linija (~3 px analizės raiškoje),
    todėl linijos ir raidės į kaukę nepatenka, o ranka ir rašiklis (~30 px) – patenka.
    """
    non_white = (flat.min(axis=2) < 200).astype(np.uint8)
    k = int(round(15 * scale)) | 1
    thick = cv2.morphologyEx(non_white, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    return skin_mask(rect) | thick


# ---------------------------------------------------------------- lapo kampų sekimas

class CornerTracker:
    """Lapo kampai UŽFIKSUOJAMI, kai keli paskutiniai aptikimai sutampa, ir nebekeičiami iki lapo keitimo.

    Kodėl fiksuojam, o ne skaičiuojam kas kadrą: kai ranka užkliudo lapo kraštą, aptikti kampai „plaukia“
    keliais px. Ištiesintas lapas tada pasislenka, 2–4 px linijos nebesutampa su patvirtintu rašalu, ir tai
    atrodo kaip dingęs rašalas → klaidingas lapo keitimas (taip ir buvo pirmame video001_calib paleidime:
    9 „lapai“ vietoj 2). Lapas per savo laiką nejuda (užklijuotas), todėl fiksuoti saugu.

    Fiksuojama greitai (≈2 s), kad neprarastume piešimo lapo pradžioje: didžiausias sutampančių aptikimų
    rinkinys (page.robust_corners). Analizei svarbiau PASTOVUMAS nei tikslumas – nedidelė kampų paklaida tik
    šiek tiek pasuka ištiesintą lapą. Tikslūs kampai galutiniam vaizdui patikslinami lapo pabaigoje iš viso
    lapo laiko aptikimų (žr. analyze → finish_page).
    """

    def __init__(self, window_s: float = 3.0, min_count: int = 8, hand_max: float = 0.03):
        self.window_s, self.min_count, self.hand_max = window_s, min_count, hand_max
        self.items: deque = deque()
        self.locked: np.ndarray | None = None

    def add(self, t: float, corners: np.ndarray | None, hand: float = 1.0) -> None:
        """hand – rankos dalis lape tame kadre. Fiksuojama tik iš kadrų, kur ranka beveik nedengia lapo:
        video001 2 lapo kampai užsifiksavo ties 11:20, kai ranka dengė 16% ir „nukando“ lapo viršų."""
        if self.locked is not None:
            return
        if corners is not None and hand < self.hand_max:
            self.items.append((t, corners))
        while self.items and self.items[0][0] < t - self.window_s:
            self.items.popleft()
        if len(self.items) >= self.min_count // 2:
            corners, n = robust_corners([c for _, c in self.items])
            if n >= self.min_count // 2:
                self.locked = corners

    def stable(self) -> np.ndarray | None:
        return self.locked

    def reset(self) -> None:
        self.items.clear()
        self.locked = None


# ---------------------------------------------------------------- vieno lapo būsena

class PageState:
    def __init__(self, shape: tuple[int, int], fc: dict, sample_fps: float):
        self.fc = fc
        self.shape = shape
        self.n_confirm = max(1, int(round(fc["confirm_s"] * sample_fps)))
        self.n_unconfirm = max(1, int(round(fc["unconfirm_s"] * sample_fps)))
        self.n_page = max(1, int(round(fc["page_missing_s"] * sample_fps)))
        self.run_len = np.zeros(shape, np.int16)
        self.run_start = np.zeros(shape, np.float32)
        self.clear_len = np.zeros(shape, np.int16)
        self.confirmed = np.zeros(shape, bool)
        self.conf_t = np.zeros(shape, np.float32)
        self.conf_color = np.zeros(shape, np.uint8)
        self.pending = np.zeros(shape, bool)
        self.last_new_t: float | None = None
        self.missing_run = 0
        self.missing_start: float | None = None
        self.finals: deque = deque()  # (t, hand_frac, raw_full) – pilnos raiškos kadrai kas ~2 s
        self.first_t: float | None = None
        self.last_t: float | None = None
        self.debug: list[tuple] = []  # kalibravimui: work/frames_debug.csv
        self.initial_flushed = False
        self.corners_a: np.ndarray | None = None  # užfiksuoti kampai (mažo kadro koordinatės)
        self.dets: list[np.ndarray] = []          # kampų aptikimai per visą lapo laiką

    def step(self, s: Sample) -> float | None:
        """Atnaujina būseną. Grąžina lapo keitimo pradžios laiką, jei aptiktas naujas lapas, kitaip None."""
        if s.ink.shape != self.shape:  # kraštutinis atvejis: kita lapo orientacija – laikom nauju lapu
            return s.t
        self.first_t = s.t if self.first_t is None else self.first_t
        self.last_t = s.t
        if self.corners_a is None:
            self.corners_a = s.corners
        if s.det is not None:
            self.dets.append(s.det)
        vis = ~s.hidden
        clear = vis & ~s.ink

        np.add(self.run_len, 1, out=self.run_len, where=s.ink & (self.run_len < 30000))
        self.run_start[s.ink & (self.run_len == 1)] = s.t
        self.run_len[clear] = 0

        newly = (self.run_len >= self.n_confirm) & ~self.confirmed
        if newly.any():
            # Jau esamų linijų pakraščiai mirga ties slenksčiu ir kartais confirm_s iš eilės „pataiko“ į rašalą.
            # Toks pikselis šalia SENO rašalo (patvirtinto prieš > halo_age_s) prijungiamas tyliai, be įvykio.
            # Šalia KĄ TIK nupiešto brūkšnio – ne: brūkšnys patvirtinamas palaipsniui per ~1 s, tai jo dalis.
            # (video001_calib: be šito 6000 px „pieštuko“ įvykiai per visą lapą.)
            old = self.confirmed & (self.conf_t < s.t - self.fc["confirm_s"] - self.fc["halo_age_s"])
            near_old = cv2.dilate(old.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
            self.confirmed |= newly
            self.conf_t[newly] = self.run_start[newly]
            self.conf_color[newly] = s.colors[newly]
            fresh = newly & ~near_old
            if fresh.any():
                self.pending |= fresh
                self.last_new_t = s.t

        # Tolerancija (match_px): ta pati linija gretimame kadre gali pasislinkti (kampų paklaida, suspaudimas).
        k = self.fc["match_px"] * 2 + 1
        ink_d = cv2.dilate(s.ink.astype(np.uint8), np.ones((k, k), np.uint8)).astype(bool)
        vis_conf = self.confirmed & vis
        n_vis = int(vis_conf.sum())
        missing = float((vis_conf & ~ink_d).sum()) / n_vis if n_vis > 300 else 0.0
        self.debug.append((round(s.t, 2), round(s.hand_frac, 4), int(s.ink.sum()), n_vis, round(missing, 3),
                           int(newly.sum()), int(self.confirmed.sum())))

        if missing > self.fc["page_missing_frac"]:
            self.missing_run += 1
            self.missing_start = self.missing_start if self.missing_start is not None else s.t
            if self.missing_run >= self.n_page:
                return self.missing_start
        else:
            self.missing_run, self.missing_start = 0, None
            # Lapas tas pats, bet kai kurie „patvirtinti“ pikseliai ilgai matomi tušti – tai buvo klaida
            # (pvz. šešėlio ar rašiklio liekana). Atšaukiam, kad nepatektų į įvykį.
            gone = vis_conf & ~ink_d
            np.add(self.clear_len, 1, out=self.clear_len, where=gone)
            self.clear_len[vis_conf & ink_d] = 0
            undo = self.confirmed & (self.clear_len >= self.n_unconfirm)
            if undo.any():
                self.confirmed[undo] = False
                self.pending[undo] = False
                self.clear_len[undo] = 0

        # Galutiniam vaizdui tinka tik kadrai, kuriuose lapas dar savo vietoje: kai lapas jau keliamas keitimui,
        # dalis rašalo dar „sutampa“ (keitimas aptinkamas tik peržengus page_missing_frac), bet jis pasislinkęs.
        # Naujausio neuždengto pikselio logika tokius kadrus paimtų pirmus (video001 2 lapas – tekstas
        # padidėjęs ir pasislinkęs, matosi stalas).
        if s.raw_full is not None and missing <= self.fc["final_missing_max"]:
            self.finals.append((s.t, s.hand_frac, s.raw_full))
            keep_from = s.t - self.fc["final_window_s"] - self.fc["page_missing_s"] - 5
            while self.finals and self.finals[0][0] < keep_from:
                self.finals.popleft()
        return None

    def ready_to_flush(self, t: float) -> bool:
        """Kada sukauptus naujus pikselius paversti įvykiais:
        - iškart po pradinio patvirtinimo (rašalas, buvęs lape nuo pradžios) – kad nesusijungtų su nauju piešimu;
        - po event_gap_s be naujo rašalo;
        - jei seniausias laukiantis pikselis senesnis nei max_event_s (piešiama be pertraukų)."""
        if not self.pending.any() or self.last_new_t is None:
            return False
        if not self.initial_flushed and t >= self.first_t + self.fc["confirm_s"] + 0.75:
            self.initial_flushed = True
            return True
        if t - self.last_new_t >= self.fc["event_gap_s"]:
            return True
        return t - float(self.conf_t[self.pending].min()) >= self.fc["max_event_s"]

    def drop_pending_after(self, t: float) -> None:
        """Lapo keitimo metu (lapas keliamas, ranka per visą kadrą) patvirtinti pikseliai – šiukšlės."""
        self.pending &= self.conf_t < t - 0.5

    def flush(self) -> list[dict]:
        """Sukaupti nauji pikseliai → įvykiai (bbox analizės koordinatėmis).

        Pirma skaidoma pagal LAIKĄ (pertrauka > event_gap_s = kitas įvykis), tik tada pagal vietą: tankiame lape
        brūkšniai, nupiešti skirtingu metu, per event_merge_px vis tiek susilietų į vieną didelį įvykį.
        """
        if not self.pending.any():
            return []
        times_all = np.unique(self.conf_t[self.pending])
        cuts = np.nonzero(np.diff(times_all) > self.fc["event_gap_s"])[0]
        bounds = np.split(times_all, cuts + 1)
        k = self.fc["event_merge_px"] * 2 + 1
        se = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        out = []
        for grp in bounds:
            sel = self.pending & (self.conf_t >= grp[0]) & (self.conf_t <= grp[-1])
            merged = cv2.dilate(sel.astype(np.uint8), se)
            n, labels, stats, _ = cv2.connectedComponentsWithStats(merged, connectivity=8)
            for i in range(1, n):
                x, y, w, h = stats[i, :4]
                sub = (labels[y:y + h, x:x + w] == i) & sel[y:y + h, x:x + w]
                cnt = int(sub.sum())
                if cnt < self.fc["event_min_px"]:
                    continue
                ys, xs = np.nonzero(sub)
                times = self.conf_t[y:y + h, x:x + w][sub]
                cols = self.conf_color[y:y + h, x:x + w][sub]
                cols = cols[cols > 0]
                color = int(np.bincount(cols).argmax()) if cols.size else 0
                out.append({
                    "bbox_a": [int(x + xs.min()), int(y + ys.min()), int(x + xs.max() + 1), int(y + ys.max() + 1)],
                    "t_start": round(float(np.percentile(times, 5)), 2),
                    "t_end": round(float(np.percentile(times, 95)), 2),
                    "color": COLOR_NAMES.get(color, "nežinoma"), "color_id": color, "ink_px": cnt,
                })
        self.pending[:] = False
        return sorted(out, key=lambda e: e["t_start"])

    def final_image(self, t_end: float, corners_full: np.ndarray) -> np.ndarray | None:
        """Galutinis lapo vaizdas: kiekvienam pikseliui – NAUJAUSIAS kadras, kuriame jo nedengia ranka.

        Kodėl ne „mažiausiai rankos“ kadrų mediana: jei piešiama iki pat lapo pabaigos, ranka yra visuose
        vėlyvuose kadruose, todėl atrenkami ankstyvi – kai lapas dar beveik tuščias (video001_calib 2 lapas).
        Naujausias neuždengtas vaizdas kiekvienam pikseliui duoda pilną IR aktualų lapą be rankos.
        Kur ranka buvo visuose buferio kadruose – mediana (geriau nei nieko).
        """
        rows = sorted((t, img) for t, _, img in self.finals if t < t_end - 1.0)
        if not rows:
            return None
        rects = [rectify(img, corners_full, long_side=FINAL_LONG_SIDE) for _, img in rows]
        # Jungiami jau IŠLYGINTI kadrai – kitaip siūlėse matytųsi apšvietimo skirtumai tarp kadrų.
        flats = [flatten(r) for r in rects]
        k = FINAL_LONG_SIDE / self.fc["analysis_long_side"]
        d = int(round(self.fc["hand_dilate_px"] * k * self.fc["final_occluder_dilate"])) * 2 + 1
        se = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (d, d))
        out = median_stack(flats[-self.fc["final_frames"]:])  # atsarginis pagrindas
        filled = np.zeros(out.shape[:2], bool)
        for rect, flat in zip(reversed(rects), reversed(flats)):
            take = ~cv2.dilate(_occluders(rect, flat, k), se).astype(bool) & ~filled
            out[take] = flat[take]
            filled |= take
            if filled.all():
                break
        return out


# ---------------------------------------------------------------- išvestis: crop'ai, miniatiūros, kontaktinis lapas

def _transform_bbox(m: np.ndarray, bbox: list[int], size: tuple[int, int]) -> list[int]:
    """bbox (x0, y0, x1, y1) per perspektyvos matricą → naujas bbox, apribotas vaizdo dydžiu (plotis, aukštis)."""
    x0, y0, x1, y1 = bbox
    pts = cv2.perspectiveTransform(np.array([[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]], np.float64), m)[0]
    w, h = size
    return [int(max(0, pts[:, 0].min())), int(max(0, pts[:, 1].min())),
            int(min(w, pts[:, 0].max())), int(min(h, pts[:, 1].max()))]


def _resize_long(img: np.ndarray, long_side: int) -> np.ndarray:
    s = long_side / max(img.shape[:2])
    return cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else img


def write_page_outputs(work: Path, page_no: int, final: np.ndarray, events: list[dict], analysis_shape, ec: dict):
    """Lapo galutinis vaizdas + kiekvieno įvykio crop ir miniatiūra. Įvykiams prirašomos export koordinatės."""
    (work / "pages_full").mkdir(parents=True, exist_ok=True)
    (work / "crops").mkdir(exist_ok=True)
    (work / "thumbs").mkdir(exist_ok=True)
    imwrite(work / "pages_full" / f"page{page_no}_final.jpg", final, 92)

    a_long = max(analysis_shape)
    to_full = max(final.shape[:2]) / a_long
    to_export = ec["page_long_side"] / a_long
    thumb_base = _resize_long(final, ec["thumb_long_side"])
    to_thumb = max(thumb_base.shape[:2]) / a_long
    for e in events:
        x0, y0, x1, y1 = e["bbox_a"]
        e["bbox"] = [int(round(v * to_export)) for v in (x0, y0, x1, y1)]
        # crop su paraštėmis – kad matytųsi, prie ko prijungta
        mx, my = max(12, (x1 - x0) * 0.15), max(12, (y1 - y0) * 0.15)
        fx0, fy0 = int(max(0, (x0 - mx) * to_full)), int(max(0, (y0 - my) * to_full))
        fx1, fy1 = int(min(final.shape[1], (x1 + mx) * to_full)), int(min(final.shape[0], (y1 + my) * to_full))
        crop = _resize_long(final[fy0:fy1, fx0:fx1], ec["crop_max_side"])
        e["crop"] = f"e{e['id']:02d}.jpg"
        imwrite(work / "crops" / e["crop"], crop, 90)
        thumb = thumb_base.copy()
        cv2.rectangle(thumb, (int(x0 * to_thumb), int(y0 * to_thumb)), (int(x1 * to_thumb), int(y1 * to_thumb)),
                      (255, 0, 255), 2)
        e["thumb"] = f"p{page_no}_e{e['id']:02d}_thumb.jpg"
        imwrite(work / "thumbs" / e["thumb"], thumb, 85)


def contact_sheets(work: Path, pages: list[dict], events: list[dict], analysis_long: int) -> list[str]:
    """Debug peržiūra autoriui: kiekvienas lapas su sunumeruotais įvykių bbox + įvykių sąrašu šone."""
    names = []
    for pg in pages:
        img = imread(work / "pages_full" / f"page{pg['page']}_final.jpg")
        if img is None:
            continue
        img = _resize_long(img, 1400)
        s = max(img.shape[:2]) / analysis_long
        evs = [e for e in events if e["type"] == "ink" and e["page"] == pg["page"]]
        for e in evs:
            if e.get("preexisting"):
                continue  # buvo lape nuo pradžios – rėmelis per visą lapą tik užgožtų tikrus įvykius
            x0, y0, x1, y1 = (int(v * s) for v in e["bbox_a"])
            c = DRAW_BGR.get(e["color_id"], (200, 0, 200))
            cv2.rectangle(img, (x0, y0), (x1, y1), c, 2)
            cv2.putText(img, str(e["id"]), (x0 + 2, max(14, y0 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 4)
            cv2.putText(img, str(e["id"]), (x0 + 2, max(14, y0 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, c, 2)
        # Šoninis sąrašas: id, laikas, spalva, gija
        side = np.full((img.shape[0], 330, 3), 255, np.uint8)
        cv2.putText(side, f"Lapas {pg['page']}  {fmt_time(pg['t_start'])}-{fmt_time(pg['t_end'])}", (8, 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)
        for i, e in enumerate(evs):
            y = 50 + i * 20
            if y > side.shape[0] - 10:
                cv2.putText(side, f"... +{len(evs) - i}", (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1)
                break
            mark = " *buvo" if e.get("preexisting") else ""
            txt = f"{e['id']:>3} {fmt_time(e['t_start'])}-{fmt_time(e['t_end'])} {_ascii(e['color'])} g{e.get('thread', '?')}{mark}"
            cv2.putText(side, txt, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, DRAW_BGR.get(e["color_id"], (0, 0, 0)), 1)
        name = f"contact_page{pg['page']}.jpg"
        imwrite(work / name, np.hstack([img, side]), 88)
        names.append(name)
    return names


def _ascii(s: str) -> str:
    """OpenCV putText nemoka lietuviškų raidžių."""
    return s.translate(str.maketrans("ąčęėįšųūžĄČĘĖĮŠŲŪŽ", "aceeisuuzACEEISUUZ"))


def assign_threads(events: list[dict]) -> None:
    """Minties gijos (CLAUDE.md): iš eilės einantys tos pačios spalvos įvykiai = viena gija; spalvos arba lapo keitimas = nauja."""
    thread, prev = 0, None
    for e in sorted((e for e in events if e["type"] == "ink"), key=lambda e: e["t_start"]):
        key = (e["page"], e["color"])
        if key != prev:
            thread += 1
            prev = key
        e["thread"] = thread


# ---------------------------------------------------------------- pagrindinis ciklas

def analyze(video: Path, work: Path, cfg: dict, progress: Progress) -> dict:
    fc, ec, rc = cfg["frames"], cfg["export"], cfg["resources"]
    # Ankstesnio paleidimo išvestis (šio modulio sugeneruota) – kad neliktų pasenusių lapų, pvz. contact_page3.jpg,
    # kai po kalibravimo lapų sumažėja.
    for sub in ("pages_full", "crops", "thumbs"):
        shutil.rmtree(work / sub, ignore_errors=True)
    for old in work.glob("contact_page*.jpg"):
        old.unlink()
    params = [cv2.CAP_PROP_HW_ACCELERATION, cv2.VIDEO_ACCELERATION_ANY] if rc["hw_decode"] else []
    cap = cv2.VideoCapture(str(video), cv2.CAP_FFMPEG, params)
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV neatidaro {video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    duration = cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps
    hw = int(cap.get(cv2.CAP_PROP_HW_ACCELERATION))
    sample_fps = fc["sample_fps"]
    full_every = int(round(sample_fps * 2))  # pilnos raiškos kadras galutiniam vaizdui – kas ~2 s

    progress.start("frames", total=round(duration, 1), unit="s video",
                   note=f"dekodavimas: {'GPU (D3D11)' if hw else 'CPU'}, gijų: {rc['frame_workers']}")

    tracker = CornerTracker()
    state: PageState | None = None
    analysis_shape = None
    page_no = 1
    pages: list[dict] = []
    events: list[dict] = []
    page_events: list[dict] = []
    ignore_until = -1.0
    next_id = 1
    main_t = 0.0
    full_scale = 2.0  # pilnas kadras / mažas kadras; nustatoma pagal pirmą kadrą

    debug_rows: list[tuple] = []

    def finish_page(t_end: float) -> bool:
        """Užbaigia lapą. False – lapas buvo pereinamasis (atmestas), numeracija nedidinama."""
        nonlocal page_events, next_id
        debug_rows.extend((page_no, *r) for r in state.debug)
        if state.first_t is None and not page_events:
            return False  # lapas be nė vieno apdoroto kadro (pvz. video pabaiga po lapo keitimo)
        state.drop_pending_after(t_end)
        evs = state.flush()
        for e in evs:
            e.update(id=next_id, type="ink", page=page_no)
            next_id += 1
        page_events += evs
        # Rašalas, patvirtintas per pirmas sekundes, buvo lape jau tada, kai prasidėjo įrašas / ištrauka
        # (ne nupieštas tuo metu) – jo laikas nieko nesako apie piešimą.
        for e in page_events:
            e["preexisting"] = e["t_start"] <= state.first_t + 0.5
        # Pereinamasis „lapas“: kampai užfiksuoti, kol lapas dar buvo dedamas; lapui nusistovėjus rašalas
        # „dingsta“ ir aptinkamas dar vienas keitimas (video001_calib: 61–66 s). Trumpas ir be naujo piešinio – atmetam.
        if (state.last_t - state.first_t) < fc["min_page_s"] and not any(not e["preexisting"] for e in page_events):
            page_events = []
            return False

        # Kampų patikslinimas iš viso lapo laiko (užfiksuoti galėjo būti paimti, kai ranka laikė kraštą).
        # Įvykių bbox perskaičiuojami į patikslintą lapą ta pačia perspektyva: M_naujas · M_senas⁻¹.
        refined, n = robust_corners(state.dets) if state.dets else (None, 0)
        if refined is None or n < 3:
            refined = state.corners_a
        a_long = fc["analysis_long_side"]
        m_old, _ = rectify_matrix(state.corners_a, a_long)
        m_new, size_new = rectify_matrix(refined, a_long)
        remap = m_new @ np.linalg.inv(m_old)
        for e in page_events:
            e["bbox_a"] = _transform_bbox(remap, e["bbox_a"], size_new)
        final = state.final_image(t_end, refined * full_scale)
        pg = {"page": page_no, "t_start": round(state.first_t if state and state.first_t is not None else 0.0, 2),
              "t_end": round(t_end, 2), "final": f"pages_full/page{page_no}_final.jpg" if final is not None else None,
              # derinimui: mažo kadro (540 px pločio) koordinatės
              "corners_locked": np.round(state.corners_a).astype(int).tolist(),
              "corners_refined": np.round(refined).astype(int).tolist(), "corners_support": n}
        if final is not None:
            write_page_outputs(work, page_no, final, page_events, analysis_shape, ec)
        pages.append(pg)
        events.extend(page_events)
        page_events = []
        return True

    def consume(t: float, fut) -> None:
        nonlocal state, analysis_shape, page_no, ignore_until, next_id, page_events
        if fut is None:
            return
        s: Sample = fut.result()
        if s.t < ignore_until:
            return
        if state is None:
            analysis_shape = s.ink.shape
            state = PageState(analysis_shape, fc, sample_fps)
        t_change = state.step(s)
        if t_change is not None:
            if finish_page(t_change):
                events.append({"id": next_id, "type": "page_change", "t": round(t_change, 2),
                               "from_page": page_no, "to_page": page_no + 1})
                next_id += 1
                page_no += 1
            state = PageState(analysis_shape, fc, sample_fps)
            tracker.reset()
            # Jau pateikti kadrai buvo ištiesinti seno lapo kampais – jų naujam lapui nenaudojam.
            ignore_until = main_t + 1.0
            return
        if state.ready_to_flush(s.t):
            for e in state.flush():
                e.update(id=next_id, type="ink", page=page_no)
                next_id += 1
                page_events.append(e)

    inflight: deque = deque()
    frame_idx = sample_idx = 0
    next_sample_t = 0.0
    last_report = 0.0
    with ThreadPoolExecutor(max_workers=rc["frame_workers"]) as pool:
        while True:
            if not cap.grab():
                break
            t = frame_idx / fps
            frame_idx += 1
            if t + 1e-6 < next_sample_t:
                continue
            next_sample_t += 1.0 / sample_fps
            ok, frame = cap.retrieve()
            if not ok:
                continue
            main_t = t
            scale = fc.get("analysis_small_w", ANALYSIS_SMALL_W) / frame.shape[1]
            full_scale = 1.0 / scale
            small = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            if tracker.stable() is None:  # kol kampai nefiksuoti – ieškom (po to – tik gijose kas ~2 s)
                c = find_page(small)
                tracker.add(t, c, hand_fraction(small, c) if c is not None else 1.0)
            corners = tracker.stable()
            fut = None
            if corners is not None:
                full = frame if sample_idx % full_every == 0 else None
                fut = pool.submit(process_sample, t, small, corners, full, fc)
            inflight.append((t, fut))
            sample_idx += 1
            while len(inflight) > rc["frame_workers"] * 2:
                consume(*inflight.popleft())
            if t - last_report >= 2.0:
                last_report = t
                n_ink = len(events) + len(page_events)
                progress.update("frames", done=round(t, 1), note=f"lapas {page_no}, įvykių {n_ink}")
        while inflight:
            consume(*inflight.popleft())
    cap.release()
    if state is not None:
        finish_page(main_t)

    with open(work / "frames_debug.csv", "w", encoding="utf-8") as f:
        f.write("page,t,hand_frac,ink_px,visible_confirmed_px,missing_frac,newly_confirmed_px,confirmed_px\n")
        f.writelines(",".join(map(str, r)) + "\n" for r in debug_rows)

    assign_threads(events)
    events.sort(key=lambda e: e.get("t_start", e.get("t", 0)))
    sheets = contact_sheets(work, pages, events, max(analysis_shape) if analysis_shape else 960)
    for e in events:
        e.pop("color_id", None)
    result = {"video": video.name, "duration": round(duration, 2), "sample_fps": sample_fps,
              "decode": "d3d11" if hw else "cpu", "coords": f"pages/*.jpg px (ilgoji kraštinė {ec['page_long_side']})",
              "pages": pages, "events": events, "contact_sheets": sheets}
    out = work / "events.json"
    tmp = out.with_suffix(".tmp.json")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(out)
    n_ink = sum(1 for e in events if e["type"] == "ink")
    progress.finish("frames", note=f"{len(pages)} lapai, {n_ink} rašalo įvykiai")
    return result
