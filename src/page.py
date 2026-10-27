"""Lapo radimas kadre ir ištiesinimas: 4 kampai -> stačiakampis, pasuktas skaitymui.

Popierius – didžiausia nesotaus (baltas/pilkas) atspalvio sritis; stalas (medis) oranžinis, todėl skiriama
pagal HSV saturation, o ne pagal šviesumą – taip veikia ir šešėlyje, kur popierius tamsesnis.
"""

from __future__ import annotations

import cv2
import numpy as np

from tools import load_config

# Kaip pasukti ištiesintą lapą, kad tekstas būtų skaitomas: config.yaml frames.read_rotation (laipsniais pagal
# laikrodžio rodyklę). Priklauso nuo to, kaip lapas guli kameros atžvilgiu.
_ROTATIONS = {0: None, 90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
READ_ROTATION = _ROTATIONS[int(load_config()["frames"].get("read_rotation", 90)) % 360]

A4_RATIO = 297 / 210


def find_page(img: np.ndarray, sat_max: int = 45, min_area_frac: float = 0.25) -> np.ndarray | None:
    """Grąžina 4 lapo kampus (float32, tvarka: viršus-kairė, viršus-dešinė, apačia-dešinė, apačia-kairė)
    originalaus kadro koordinatėmis arba None, jei lapo nerasta.

    sat_max – kiek sotus gali būti popierius (0–255). Medis ~90+, popierius < 30 (įvertinimas iš video001).
    Ranka ant lapo kraštų „įkanda“ į kontūrą, todėl imamas išgaubtas apvalkalas (convex hull) ir iš jo 4 kampai.
    """
    h, w = img.shape[:2]
    scale = 480 / max(h, w)
    small = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(cv2.GaussianBlur(small, (5, 5), 0), cv2.COLOR_BGR2HSV)
    mask = ((hsv[..., 1] < sat_max) & (hsv[..., 2] > 60)).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    c = max(contours, key=cv2.contourArea)
    if cv2.contourArea(c) < min_area_frac * small.shape[0] * small.shape[1]:
        return None

    # Lapas dažnai išeina už kadro krašto (video001 – kairėje ir dešinėje). Tikrasis kampas tada už kadro, o jo
    # kraštinė matoma tik trumpa atkarpa: jos kryptis triukšminga, sankirta šokinėja ±50–75 px. Todėl kadro
    # krašto atkarpa leidžiama laikyti lapo kraštine: kampas = lapo krašto ir kadro krašto sankirta, ji visada
    # kadre ir pastovi. Ištiesinamas MATOMAS lapas – to ir reikia (kas už kadro, vis tiek nematoma).
    hull = cv2.convexHull(c)
    pts = cv2.approxPolyDP(hull, 0.01 * cv2.arcLength(hull, True), True).reshape(-1, 2).astype(np.float32)
    quad = _quad_from_sides(pts)
    if quad is None:
        return None
    quad = _order(quad)
    if not _plausible(quad, small.shape[1], small.shape[0], min_area_frac):
        return None
    return quad / scale


def _plausible(q: np.ndarray, w: int, h: int, min_area_frac: float) -> bool:
    """Ar keturkampis gali būti lapas. Kai ranka kerta lapo kraštą, kraštinių sankirtos duoda šiukšles
    (video001 2 lapas: du vienodi kampai [88,-659], kampai už kadro, „nukąstas“ lapas be viršaus).
    Tokį užfiksavus, analizė mato tik dalį lapo, o bbox perskaičiavimas per beveik išsigimusią matricą sugenda."""
    mx, my = 0.05 * w, 0.05 * h
    if (q[:, 0] < -mx).any() or (q[:, 0] > w - 1 + mx).any() or (q[:, 1] < -my).any() or (q[:, 1] > h - 1 + my).any():
        return False
    dmin = min(np.linalg.norm(q[i] - q[j]) for i in range(4) for j in range(i + 1, 4))
    if dmin < 0.3 * min(w, h):
        return False
    if not cv2.isContourConvex(q.reshape(-1, 1, 2).astype(np.float32)):
        return False
    return cv2.contourArea(q.astype(np.float32)) >= min_area_frac * w * h


def _quad_from_sides(pts: np.ndarray) -> np.ndarray | None:
    n = len(pts)
    sides = []
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        sides.append((float(np.linalg.norm(b - a)), i, a, b))
    if len(sides) < 4:
        return None
    # 4 ilgiausios kraštinės, grąžinamos apvalkalo eilės tvarka (kad gretimos susikirstų kampuose).
    four = sorted(sorted(sides, reverse=True)[:4], key=lambda s: s[1])
    corners = []
    for k in range(4):
        _, _, a1, b1 = four[k]
        _, _, a2, b2 = four[(k + 1) % 4]
        p = _intersect(a1, b1, a2, b2)
        if p is None:
            return None
        corners.append(p)
    return np.array(corners, dtype=np.float32)


def _intersect(a1, b1, a2, b2):
    """Dviejų tiesių (per a1-b1 ir a2-b2) sankirta; None, jei beveik lygiagrečios."""
    d1, d2 = b1 - a1, b2 - a2
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-6 * (np.linalg.norm(d1) * np.linalg.norm(d2) + 1e-9):
        return None
    t = ((a2[0] - a1[0]) * d2[1] - (a2[1] - a1[1]) * d2[0]) / den
    return a1 + t * d1


def find_page_stable(frames: list[np.ndarray]) -> np.ndarray | None:
    """Lapo kampai iš daugelio to paties lapo kadrų: kiekvieno kampo koordinačių mediana.

    Lapas užklijuotas ir per savo „gyvenimą“ nejuda, bet ranka kartais uždengia kampą – tame kadre kampas
    randamas klaidingai. Mediana per kadrus (geriausia – išbarstytus per visą lapo laiką) tokius atvejus atmeta,
    jei ranka kampą dengia mažiau nei pusėje kadrų. NEIMTI kampų iš kelių kadrų medianos vaizdo: jei ranka
    tuo metu laiko kampą, jis nupjaunamas ir hand_fraction() rankos ten nebemato.
    """
    found = [c for c in (find_page(f) for f in frames) if c is not None]
    if not found:
        return None
    return np.median(np.stack(found), axis=0).astype(np.float32)


def hand_fraction(img: np.ndarray, corners: np.ndarray) -> float:
    """Kokią lapo dalį uždengia ranka (0–1). Naudojama kadrams be rankos atrinkti (galutiniam lapo vaizdui).

    Oda = šiltas atspalvis (H 0–25 / 165–180) su vidutiniu sodrumu. Raudonas flomasteris irgi patenka į tą
    atspalvį, bet jo linijos plonos – morfologinis open (didelis branduolys) palieka tik dideles dėmes (ranką).
    Skaičiuojama tik lapo viduje: stalas (medis) yra tokio pat atspalvio kaip oda.
    """
    h, w = img.shape[:2]
    scale = 360 / max(h, w)
    small = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    skin = skin_mask(small)
    page = np.zeros_like(skin)
    cv2.fillConvexPoly(page, (corners * scale).astype(np.int32), 1)
    area = int(page.sum())
    return float((skin & page).sum()) / area if area else 0.0


def skin_mask(img: np.ndarray) -> np.ndarray:
    """Odos (rankos) kaukė 0/1 tos pačios raiškos kaip img. Žr. hand_fraction() – kodėl tokie slenksčiai.
    Morfologijos branduolys proporcingas vaizdo dydžiui: plonas raudonas brūkšnys turi išnykti bet kokioje raiškoje."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    skin = (((hue <= 25) | (hue >= 165)) & (sat > 40) & (sat < 200) & (val > 60)).astype(np.uint8)
    k = max(3, int(round(max(img.shape[:2]) / 50)) | 1)  # 360 px → 7, 960 px → 19
    return cv2.morphologyEx(skin, cv2.MORPH_OPEN, np.ones((k, k), np.uint8))


def _order(pts: np.ndarray) -> np.ndarray:
    """Kampų tvarka: viršus-kairė, viršus-dešinė, apačia-dešinė, apačia-kairė."""
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]], dtype=np.float32)


def rectify_matrix(corners: np.ndarray, long_side: int = 1600, inset_frac: float = 0.004):
    """Viena 3x3 matrica: kadras → ištiesintas, apkarpytas ir skaitymui pasuktas lapas. Grąžina (M, (plotis, aukštis)).

    Viena matrica (o ne warp + crop + rotate atskirai) reikalinga tam, kad būtų galima perskaičiuoti koordinates
    tarp dviejų skirtingų kampų rinkinių: x_naujas = M_naujas · M_senas⁻¹ · x_senas (žr. frames.py, bbox
    perskaičiavimas po kampų patikslinimo).
    inset_frac – nupjaunama nuo kraštų (ilgosios kraštinės dalimis), kad neliktų stalo juostelės.
    """
    inset = max(1, int(round(long_side * inset_frac)))
    tl, tr, br, bl = corners
    portrait = (np.linalg.norm(bl - tl) + np.linalg.norm(br - tr)) >= (np.linalg.norm(tr - tl) + np.linalg.norm(br - bl))
    W, H = (int(long_side / A4_RATIO), long_side) if portrait else (long_side, int(long_side / A4_RATIO))
    dst = np.array([[0, 0], [W - 1, 0], [W - 1, H - 1], [0, H - 1]], dtype=np.float32)
    M = cv2.getPerspectiveTransform(corners.astype(np.float32), dst)
    Wc, Hc = W - 2 * inset, H - 2 * inset
    T = np.array([[1, 0, -inset], [0, 1, -inset], [0, 0, 1]], dtype=np.float64)
    if READ_ROTATION == cv2.ROTATE_90_CLOCKWISE:
        R, size = np.array([[0, -1, Hc - 1], [1, 0, 0], [0, 0, 1]], dtype=np.float64), (Hc, Wc)
    elif READ_ROTATION == cv2.ROTATE_90_COUNTERCLOCKWISE:
        R, size = np.array([[0, 1, 0], [-1, 0, Wc - 1], [0, 0, 1]], dtype=np.float64), (Hc, Wc)
    elif READ_ROTATION == cv2.ROTATE_180:
        R, size = np.array([[-1, 0, Wc - 1], [0, -1, Hc - 1], [0, 0, 1]], dtype=np.float64), (Wc, Hc)
    else:
        R, size = np.eye(3), (Wc, Hc)
    return R @ T @ M, size


def rectify(img: np.ndarray, corners: np.ndarray, long_side: int = 1600, inset_frac: float = 0.004) -> np.ndarray:
    """Ištiesina lapą į A4 proporcijų stačiakampį ir pasuka skaitymui (READ_ROTATION).

    corners – tos pačios raiškos kaip img. Kraštų nupjovimas proporcingas, todėl analizės (960) ir galutinio
    (1600) lapo koordinatės skiriasi tik mastelio koeficientu.
    """
    M, size = rectify_matrix(corners, long_side, inset_frac)
    # Už kadro esanti dalis (jei kampas netikslus) užpildoma balta (popierius), ne pakartotu kraštu – be dryžių.
    return cv2.warpPerspective(img, M, size, flags=cv2.INTER_AREA,
                               borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))


def robust_corners(dets: list[np.ndarray], tol_frac: float = 0.01) -> tuple[np.ndarray | None, int]:
    """Kampai iš daug aptikimų: didžiausias tarpusavyje sutampančių aptikimų rinkinys → jo mediana.

    Ranka lapo kaukę tik „nukanda“, ir kiekvieną kartą skirtingai, todėl klaidingi aptikimai tarpusavyje
    nesutampa, o švarūs (be rankos) – sutampa (video001: be rankos ±10 px, su ranka – iki 250 px).
    Grąžina (kampai, rinkinio dydis).
    """
    if not dets:
        return None, 0
    stack = np.stack(dets)
    diag = float(np.median([np.linalg.norm(c[2] - c[0]) for c in dets]))
    tol = tol_frac * diag
    # dev[i, j] = didžiausias kampo skirtumas tarp aptikimų i ir j
    dev = np.abs(stack[:, None] - stack[None, :]).max(axis=(2, 3))
    counts = (dev < tol).sum(axis=1)
    best = int(np.argmax(counts))
    members = stack[dev[best] < tol]
    return np.median(members, axis=0).astype(np.float32), int(counts[best])
