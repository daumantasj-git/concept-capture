"""Apšvietimo išlyginimas lapų kadrams: saulės dėmės, šešėliai ir spalvinis atspalvis -> tolygiai baltas popierius.

Tikras HDR čia negalimas (video – viena užrakinta ekspozicija), todėl daroma tai, ką darytų dokumentų skeneris:
  1. flatten():     įvertinamas „tuščio popieriaus“ fonas ir kadras padalinamas iš jo (kiekvienas kanalas atskirai).
  2. median_stack(): kelių kadrų mediana – judanti ranka ir jos šešėlis išnyksta, nejudantis rašalas lieka.

Naudojama ir kadrų analizėje (diff'as nebemirga nuo šešėlių), ir export'e (pages/*.jpg).
"""

from __future__ import annotations

import cv2
import numpy as np


def _odd(n: float) -> int:
    n = max(3, int(round(n)))
    return n if n % 2 else n + 1


def estimate_background(img: np.ndarray, stroke_frac: float = 0.02, work_scale: float = 0.25,
                        ink_ratio: float = 0.85) -> np.ndarray:
    """Popieriaus apšvietimo įvertinimas be rašalo. Dviem žingsniais:

    1. Grubus fonas: rašalas tamsesnis už popierių, todėl max filtras (dilate), platesnis nei linijos storis,
       jį „užlieja“ fonu. Bet spalvotas rašalas viename kanale būna šviesus (raudonas – R kanale), todėl
       to kanalo fonas šalia rašalo išpučiamas → popierius aplink gauna atspalvį (melsvą aureolę).
    2. Rašalo kaukė: pikseliai, kurių bent vienas kanalas < ink_ratio grubaus fono (rašalas bet kokia spalva,
       ranka). Galutinis fonas – normalized convolution tik per švarius popieriaus pikselius:
       blur(img·paper) / blur(paper). Rašalas fono nebeiškreipia, todėl galima mažesnis blur ir aštresnis
       šešėlio kraštas lieka mažiau matomas.

    Skaičiuojama sumažintame vaizde: fonas lėtai kintantis, o 1080x1920 dilate su dideliu branduoliu būtų lėtas.
    stroke_frac – branduolio dydis trumposios kraštinės dalimis. 0.02 ≈ 20 px 1080 px pločiui:
    platesnis nei flomasterio linija (~4–8 px), bet siauresnis nei rankos šešėlio perėjimas (įvertinimas, kalibruoti).
    """
    h, w = img.shape[:2]
    small = cv2.resize(img, None, fx=work_scale, fy=work_scale, interpolation=cv2.INTER_AREA).astype(np.float32)
    k = _odd(min(h, w) * work_scale * stroke_frac)

    rough = cv2.dilate(small, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    rough = cv2.GaussianBlur(cv2.medianBlur(rough.astype(np.uint8), _odd(k * 3)).astype(np.float32), (0, 0), k)

    # Rašalo kaukė pagal FORMĄ, ne pagal tamsumą: black-hat randa tik tamsius darinius, siauresnius už k
    # (linijas, raides). Platus šešėlis ir jo aštrus kraštas black-hat'e ≈ 0, todėl lieka „popieriumi“ ir
    # fonas ten įvertinamas teisingai. (Lyginant su grubiu fonu, tamsioji šešėlio krašto pusė atrodytų kaip
    # rašalas, tada ten nelieka popieriaus pavyzdžių ir lieka pilka juosta.)
    darkest = small.min(axis=2)  # tamsiausias kanalas: bet kokios spalvos rašalas jame tamsus
    blackhat = cv2.morphologyEx(darkest, cv2.MORPH_BLACKHAT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    local_paper = cv2.morphologyEx(darkest, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    ink = blackhat > (1.0 - ink_ratio) * np.maximum(local_paper, 1.0)
    # Ranka ir stalas – platūs, black-hat jų nepagauna, bet jie sotūs (popierius ir šešėlis – ne).
    sat = cv2.cvtColor(small.astype(np.uint8), cv2.COLOR_BGR2HSV)[..., 1]
    ink = (ink | (sat > 60)).astype(np.uint8)
    ink = cv2.dilate(ink, np.ones((3, 3), np.uint8), iterations=2)  # ir rašalo kraštai (antialias)
    paper = (1 - ink).astype(np.float32)

    def paper_only(sigma):
        num = cv2.GaussianBlur(small * paper[..., None], (0, 0), sigma)
        den = cv2.GaussianBlur(paper, (0, 0), sigma)[..., None]
        return num / np.maximum(den, 1e-6), den

    def paper_only_edges(radius):
        # Tas pats, tik guided filter vietoj Gaussian: vadovas (guide) – šviesumas su rašalu, „užlietu“ fonu.
        # Filtras nelieja per ryškias šviesumo ribas, todėl aštrus saulės/šešėlio kraštas (lango rėmo šešėlis)
        # nebesusimaišo į pilką juostą. Rašalas iš vadovo pašalintas, kad jo kraštai nelaikytų fono.
        guide = np.where(paper[..., None] > 0, small, rough).mean(axis=2).astype(np.float32)
        eps = (0.03 * 255) ** 2
        gf = cv2.ximgproc.guidedFilter
        num = gf(guide, small * paper[..., None], radius, eps)
        den = gf(guide, paper, radius, eps)[..., None]
        return num / np.maximum(den, 1e-6), den

    near, den_near = paper_only_edges(int(k * 1.2))
    # Kur aplink beveik nėra švaraus popieriaus (maži užrašyti langeliai, ranka) – tas pats tik per platesnį
    # spindulį. NE grubus fonas: jo R kanalas šalia raudono rašalo išpūstas ir duotų melsvą atspalvį.
    far, den_far = paper_only(k * 3)
    bg = np.where(den_near > 0.15, near, np.where(den_far > 0.02, far, rough))
    return cv2.resize(bg, (w, h), interpolation=cv2.INTER_LINEAR)


def flatten(img: np.ndarray, gamma: float = 1.6, white: float = 0.92, stroke_frac: float = 0.02) -> np.ndarray:
    """BGR kadras -> išlygintas BGR kadras (popierius 255, rašalas su savo spalva).

    Dalinama kiekvienu kanalu atskirai: raudonas flomasteris R kanale lieka šviesus (≈ popierius), G/B tamsūs,
    todėl spalva išsilaiko, o šiltas saulės / šaltas šešėlio atspalvis susivienodina (balto balanso efektas).
    white – santykis su fonu, nuo kurio pikselis laikomas grynai baltu: popieriaus tekstūra ir šešėlio
    likučiai (0.92–1.0) išnyksta, rašalas (< ~0.85) nepaliečiamas.
    gamma > 1 patamsina tarpinius tonus – blankus pieštukas tampa matomesnis, baltas popierius lieka baltas.
    """
    bg = estimate_background(img, stroke_frac=stroke_frac)
    ratio = img.astype(np.float32) / np.maximum(bg, 1.0)
    out = np.clip(ratio / white, 0.0, 1.0) ** gamma
    return (out * 255.0 + 0.5).astype(np.uint8)


def median_stack(frames: list[np.ndarray]) -> np.ndarray:
    """Kelių (jau sulygintų, to paties lapo) kadrų mediana kiekvienam pikseliui.

    Ranka ar jos šešėlis, uždengiantis vietą mažiau nei pusėje kadrų, išnyksta.
    Rašalas, atsiradęs lango viduryje, rodomas tik jei matomas daugiau nei pusėje kadrų –
    todėl galutiniam lapo vaizdui imti kadrus po paskutinio piešimo, prieš lapo keitimą.
    """
    if len(frames) == 1:
        return frames[0]
    return np.median(np.stack(frames, axis=0), axis=0).astype(np.uint8)
