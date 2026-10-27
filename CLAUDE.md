# CLAUDE.md — Koncepcijų video analizė (concept-capture)

## Asmeniniai nustatymai
Jei repo šaknyje yra **`ASMENINIS.md`**, perskaityk jį sesijos pradžioje. Ten yra naudotojo bendravimo pageidavimai, jo kompiuterio ypatybės ir filmavimo įpročiai. Jei jie prieštarauja šiam failui, galioja `ASMENINIS.md`.
`ASMENINIS.md` laikomas tik asmeniniame branch'e (`personal/<vardas>`), `main`'e jo nėra (žr. „Git“).

**Į git (nė į vieną branch'ą) nekeliama:** video, garsas, transkripcijos, įrašų turinys (`sessions/`) ir firmų / darbo sistemų pavadinimai. Tokie dalykai laikomi aplanke **`asmeninis/`** (`.gitignore`), pvz. `asmeninis/vocab.txt` (asmeninis žodynas, prijungiamas prie `vocab.txt` automatiškai). Kalibravimo pastabos `config.yaml` ir kode gali minėti įrašą (pvz. „video001: 131 įvykis“), bet tik techniškai, be turinio.

## Tikslas
Autorius (šio kompiuterio naudotojas) nusifilmuoja (~15 min), kaip popieriaus lapuose schemiškai, spalvomis piešia savo koncepciją ir kartu ją pasakoja lietuviškai.
Šis projektas iš video sukuria **kompaktišką, AI skaitomą medžiagą**: kas nupiešta, kur, kada ir ką autorius tuo metu sakė.
Tada analizės agentas kritikuoja koncepciją, randa, kas pamiršta, ir siūlo supaprastinimų.
Galutinis rezultatas: aplankas su `concept.md` ir lapų nuotraukomis, kurį galima įdėti į **kitą** Claude Code projektą.

Infografikai ir animacijos bus vėlesnė fazė, dabar jų nedarom.

## Bendravimas
- Su autoriumi bendrauk lietuviškai. IT terminus palik angliškai.
- Neblokuok darbo klausimais: pasirink labiausiai tikėtiną variantą ir įvardink prielaidą.
- Skaičius ir slenksčius žymėk: **patvirtinta** (šaltinis arba matavimas), **įvertinimas** arba **nuomonė**. Taip pažymėti ir `config.yaml` komentarai.

## Aplinka
- **Windows 10/11**, PowerShell. Pradinis paruošimas: `setup.ps1` (žr. `README.md`). Jis įdiegia Python 3.13, ffmpeg, Node.js, `.venv`, parsiunčia whisper modelį ir paleidžia `src\machine.py`.
- **Šio kompiuterio aparatūra ir parinkti parametrai** yra `config.local.yaml`: CPU, RAM, GPU, ar veikia CUDA ir GPU dekodavimas, gijų skaičius. Jį sugeneruoja `src\machine.py`, į git jis nepatenka. Jei failo nėra, pasiūlyk paleisti `setup.ps1`.
- Python: visada per `.venv\Scripts\python.exe`. Paketai yra `requirements.txt`.
  **Nediegti `opencv-python` kartu su `opencv-contrib-python`.** Abu rašo į tą patį `cv2` modulį ir sugadina vienas kitą. Reikia contrib (`cv2.ximgproc`).
- Whisper: CPU (`int8`) arba NVIDIA CUDA (`float16`), jei `src\machine.py` patikrino, kad CUDA veikia. Ilgus procesus leisk, kai nešiojamas kompiuteris įjungtas į maitinimą, nes nuo baterijos CPU stabdomas.
- **Spąstai (patvirtinta 2026-10-27):**
  - `cv2.imwrite`/`imread` Windows'e **nemoka ne-ASCII kelių** (pvz. „ė“ kelyje). Jie tyliai grąžina False, ir failas neatsiranda. Visada naudok `tools.imwrite`/`tools.imread`.
  - faster-whisper 1.2.1 + PyAV 19 nesuderinami (`av.open()` nebepriima `metadata_errors`). Todėl whisper'iui paduodamas numpy masyvas iš `audio.wav` (`audio.load_wav`), ne failo kelias.
  - ffmpeg, įdiegtas per winget, senesnėje terminalo sesijoje gali nesimatyti PATH'e. `tools.find_exe` jį randa ir winget kataloge.

## Filmavimo susitarimai (autoriaus pusė)
- Kamera stovi **fiksuotai** ir nejuda. Lapas per savo laiką nejuda (užklijuotas), bet **naujas lapas gali būti padėtas kiek kitaip** (pasislinkęs, pasviręs). Todėl kampai randami kiekvienam lapui atskirai.
- Telefone **užrakintas exposure/focus (AE/AF lock)**. Šviesa gali būti **netolygi** (saulė, lango rėmo šešėliai, rankos šešėlis). Tai sprendžia apšvietimo išlyginimas (`src/flatten.py`), o ne filmavimo sąlygos.
- Filmuojama **vertikaliai** (rotation −90, OpenCV/ffmpeg pasuka patys). Kiek dar pasukti ištiesintą lapą skaitymui, nustato `frames.read_rotation` (`config.yaml`, numatyta 90° pagal laikrodžio rodyklę).
- Piešiama **spalvotais flomasteriais ir pieštuku**. Pieštukas blankus, todėl slenksčiai skaičiuojami ant išlygintų kadrų.
- **Galiuko sekimo nėra.** Kada ir kur piešta, nustatoma iš **atsiradusio rašalo**, o ne iš rašiklio galiuko.
- **Gestų (rodymo pirštu be rašalo) nefiksuojame.**
- **Fiksuotos spalvų legendos nėra.** Spalva žymi **minties giją**: kas nupiešta ta pačia spalva maždaug tuo pačiu metu, priklauso tai pačiai tuo metu dėstomai minčiai.
  - Spalvos **pakeitimas** = tikėtina naujos minties pradžia (įvertinimas, tikrinti su kalba).
  - Ta pati spalva skirtingu laiku **nebūtinai** reiškia tą pačią temą. Spalva yra tik **laiko + spalvos** grupavimo požymis.
  - Iš spalvos **nespėk reikšmės**, pvz. „raudona = ryšys“. Rodyklės ir ryšiai būna bet kokios spalvos.
- Autorius stengiasi neuždengti piešiamos vietos ir **daro pauzes** kalboje. Tai normalu.
- Pasirinktinai: pabaigoje kiekvieną lapą nufotografuoja telefonu (`page1.jpg`…). Tos nuotraukos turi prioritetą prieš video kadrus.
- Video ir nuotraukos įkeliamos telefonu per vietinį serverį (`serveris/`, žr. jo README.md). Jos patenka į `sessions/videoNNN/`. Įkėlimas apdorojimo **nepaleidžia** (žr. „Kada apdoroti“).

## Pipeline

```
video.mp4 ─┬─► [1] ffprobe meta
           ├─► [2] audio → faster-whisper (LT, word timestamps, VAD) ─► transcript.json
           └─► [3] kadrai → lapas / išlyginimas / ranka / stabilumas / diff ─► events.json + crops
                         [4] alignment (įvykis ↔ kalbos langas) ─► evidence.md
                         [5] analizės subagentas ─► analysis.md
                         [6] export ─► sessions/videoNNN/concept.md + pages/
```

### [2] Garsas
- `ffmpeg -i video.mp4 -ac 1 -ar 16000 audio.wav`
- `faster-whisper`, modelis **`large-v3-turbo`**, `language="lt"`. Įrenginys ir tikslumas (`device`, `compute_type`) iš `config.local.yaml`.
- Garsas dalinamas į `audio.parallel_parts` dalių tyliausiose vietose, ir dalys transkribuojamos lygiagrečiai (žr. `config.yaml` matavimus).
- `vad_filter=True`: išmeta tylą ir mažina „prisigalvotą“ tekstą pauzėse.
- `word_timestamps=True`.
- `initial_prompt` + `hotwords`: autoriaus žodynas iš `vocab.txt`.
- Jei lietuviškai klaidų per daug, naudok atsarginį `large-v3`. Jis keletą kartų lėtesnis (įvertinimas).
- Slengo („seivinti“, „mergint“) **netaisyk** ir nekeisk transkripcijoje.

### [3] Kadrai: lokali analizė (AI jų nemato)
Lokaliai galima apdoroti daug kadrų, nes tai nieko nekainuoja. AI gauna tik atrinktą rezultatą.

| Žingsnis | Metodas | Pradinė reikšmė (įvertinimas, kalibruoti) |
|---|---|---|
| Sampling | OpenCV skaito kadrus (GPU dekodavimas, jei veikia) | 4 fps |
| Lapo radimas | `page.find_page_stable`: HSV saturation kaukė (popierius nesotus, medis sotus) → 4 tikros lapo kraštinės → jų sankirtos (kampas gali būti **už kadro**) → kampų mediana per lapo kadrus | sat < 45 |
| Ištiesinimas | `page.rectify`: perspektyva į A4 + pasukimas skaitymui (`frames.read_rotation`). **Visos koordinatės (bbox) yra ištiesinto lapo**, kaip `pages/*.jpg` | 90° CW |
| Apšvietimo išlyginimas | `flatten.flatten`: popieriaus fonas iš švarių pikselių (rašalas atpažįstamas pagal formą, black-hat), dalyba per kanalus, baltos ribos taškas, gamma | gamma 1.6, white 0.92 |
| Rankos kaukė | `page.hand_fraction`: odos atspalvis lapo viduje + morfologija (ploni raudoni brūkšniai atmetami). Ranka išmetama iš diff'o | — |
| Patvirtintas rašalas | pikselis = rašalas, jei `confirm_s` iš eilės matomas kaip plona linija (black-hat) ir nedengiamas rankos; pirmo pasirodymo laikas = kada nupiešta. Pakraščių mirgėjimas šalia seno rašalo ignoruojamas (`halo_age_s`) | 2 s |
| Įvykiai | nauji patvirtinti pikseliai → pirma skaidoma pagal laiką (pertrauka > `event_gap_s`), tada pagal vietą (`event_merge_px`) | 3 s, 15 px, min 40 px |
| Lapo keitimas | dingo > `page_missing_frac` matomo patvirtinto rašalo `page_missing_s` iš eilės. Pereinamasis lapas (< `min_page_s`, be naujo piešinio) atmetamas | 50%, 2 s, 10 s |
| Kampai | analizei užfiksuojami po ~2 s (pastovumas svarbiau už tikslumą); lapo pabaigoje patikslinami iš viso lapo aptikimų (`page.robust_corners`), bbox perskaičiuojami per perspektyvos matricą | — |
| Galutinis lapo vaizdas | kadrai kas ~2 s iš paskutinės lapo minutės → ištiesinti → išlyginti → kiekvienam pikseliui **naujausias, nedengtas rankos ar rašiklio** (`_occluders`) → ≤800 px | — |
| Rašalo spalva | dominuojanti naujo rašalo spalva (HSV), klasės: juoda / pieštukas / raudona / mėlyna / žalia / kita | — |
| Minties gijos | iš eilės einantys tos pačios spalvos įvykiai = viena gija; spalvos pakeitimas = gijos riba | — |

**Įvykių tipai:**
- `ink`: tarp dviejų stabilių būsenų atsirado naujo rašalo. `t_start`–`t_end` yra laikas, kada tai piešta. Fiksuojam crop + bbox + spalvą.
- `page_change`: naujas lapas. Prieš keitimą išsaugom **galutinį ankstesnio lapo vaizdą**.

`events.json` įrašo pavyzdys (bbox ištiesinto lapo koordinatėmis):
```json
{"id": 12, "type": "ink", "t_start": 520.0, "t_end": 524.1, "page": 1,
 "bbox": [210, 300, 490, 345], "color": "red", "thread": 4,
 "crop": "e12.jpg", "thumb": "p1_e12_thumb.jpg"}
```

### [4] Alignment (piešinys ↔ žodžiai)
- Įvykiai jungiami į blokus (iš eilės tos pačios gijos įvykiai viename lape), kiekvienam blokui imama kalba prieš / piešiant / po (`config.yaml` `align`). Autorius dažnai pirma pasako, tada piešia.
- **Neprisirišinėk prie tikslių sekundžių.**

### Tokenų taisyklės
- Claude tokenai priklauso nuo **pikselių** (`w×h/750`, patvirtinta Anthropic docs), o ne nuo spalvų ar failo dydžio.
- **Spalvų nemažinti ir nedaryti grayscale.** Tai tokenų nesutaupo, o spalvos rodo minties gijas.
- Taupoma taip: crop'ai, miniatiūros ~400 px su pažymėtu bbox, lapo vaizdai ≤ ~800 px ilgoji kraštinė, dedupe, tuščių kadrų išmetimas.
- Crop'as visada keliauja kartu su lapo miniatiūra. Be konteksto rodyklė ar langelis neturi prasmės.
- Niekada nesiųsk AI visų kadrų (15 min × 2 fps = 1800 kadrų ≈ 2,7M tokenų, netilps).

### [5] Analizės subagentas
- Paleidžiamas atskiras subagentas su švariu kontekstu, jam paduodamas `evidence.md` + crop'ai + lapų vaizdai.
- Jo išvesties šablonas (tikslios antraštės `## 1.`–`## 5.`, jas skaido `export.py`):
  1. Koncepcijos santrauka (5–10 sakinių)
  2. Ko trūksta / kas pamiršta
  3. Rizikos ir prieštaravimai
  4. Kaip supaprastinti
  5. Atviri klausimai autoriui
- **Griežtai atskirti** tai, kas užfiksuota (piešinys + žodžiai), nuo AI interpretacijos.
- Spalvas naudok tik minties gijoms atpažinti, **ne kategorijoms**. Prasmę imk iš žodžių ir užrašų.

### [6] Export kitam projektui
Rezultatas rašomas **į tą patį sesijos katalogą**, šalia video. Kitam projektui kopijuojami tik `concept.md` + `pages/`, kaip aplankas, **ne zip** (zip tik persiuntimui):
```
sessions/video001_processed/
├── concept.md
└── pages/   page1_final.jpg, page2_final.jpg, ...   (≤ ~800 px)
```
Crop'ai lieka `work/` aplanke ir į export nepatenka.

## `concept.md` struktūra
`src/export.py` generuoja: YAML header (source_video, recorded, duration, pages, whisper_model, pipeline_version, generated) → „Kas čia“ → „Kaip skaityti“ (koordinatės, spalvos = minties gijos, įvykių tipai, pasitikėjimo žymės [užfiksuota] [AI interpretacija] [neaišku – klausti autoriaus]) → „Kaip gauta“ → „Kaip naudoti kitame projekte“ → lapų suvestinė → įvykiai (EVENT 01…N) → pilna transkripcija → AI interpretacija → atviri klausimai autoriui.

## Projekto struktūra
Struktūra sąmoningai plokščia: **vienas įrašas = vienas katalogas**, viduje daugiausia du sub-folderiai.
```
README.md         trumpa instrukcija žmogui (diegimas, filmavimas, naudojimas)
setup.ps1         vienkartinis paruošimas: programos, .venv, modelis, kompiuterio įvertinimas
requirements.txt  Python paketai
serveris/         telefono įkėlimo serveris (Node, prievadas 8766), žr. serveris/README.md
src/
  machine.py      kompiuterio įvertinimas → config.local.yaml
  audio.py        [2]
  frames.py       [3]
  align.py        [4] įvykiai → blokai (minties gijų atkarpos) + kalba prieš / piešiant / po
  evidence.py     evidence.md surinkimas (tai, ką mato analizės subagentas)
  export.py       [6]
  progress.py     eigos rašymas į work/progress.json (rodo serveris /eiga)
  flatten.py      apšvietimo išlyginimas (šešėliai, saulė) + laiko mediana (ranka)
  page.py         lapo radimas, ištiesinimas, pasukimas skaitymui, rankos ploto matavimas
  run.py          visas pipeline, CLI: .venv\Scripts\python.exe src\run.py sessions\video001
config.yaml       visi slenksčiai ir parametrai vienoje vietoje (bendri)
config.local.yaml šio kompiuterio parametrai (sugeneruotas, į git nepatenka)
vocab.txt         bendras žodynas whisper'iui
asmeninis/        į git nepatenka: vocab.txt (asmeniniai terminai, firmų pavadinimai), privatūs užrašai
ASMENINIS.md      (tik asmeniniame branch'e) naudotojo pageidavimai ir įpročiai, be firmų duomenų
sessions/         į git nekeliama
  video001/       sukuria serveris automatiškai (numeris = didžiausias esamas + 1)
    video.mp4       originalas (iPhone: video.mov). Kol keliasi, vadinasi video.mp4.part, jo neliesti
    info.json       originalus failo vardas, dydis, įkėlimo laikas
    page1.jpg …     (pasirinktinai) lapų nuotraukos iš telefono
    concept.md      ◄ galutinis rezultatas [6]
    pages/          ◄ galutiniai lapų vaizdai ≤800 px [6]
    work/           tarpiniai failai: audio.wav, transcript.json, events.json, crops, contact sheet, evidence.md, analysis.md
  videoNNN_calib/ (pasirinktinai) trumpa ištrauka kalibravimui (`ffmpeg -ss … -t 120 -c copy`).
                  Serveris jos nemato ir numeracijai įtakos neturi. Į `_processed` nepervadinama.
```
Apdorotas įrašas pervadinamas į `video001_processed/`. Numeris išlieka, serveris tokių numerių nekartoja.

## Kada apdoroti: tik autoriui paprašius
- **Niekada nepradėk apdoroti savaime**, net jei pamatai naują video `sessions/`. Įkėlimas ≠ leidimas apdoroti.
- Kai autorius klausia „ar yra naujų?“ (ar panašiai), parodyk lentelę su visais `sessions/video*` katalogais **be** `_processed`:
  id, originalus vardas ir dydis (`info.json`), trukmė (`ffprobe`), įkėlimo laikas, lapų nuotraukų skaičius.
  Jei kataloge dar yra `video.*.part`, tai įkėlimas dar vyksta arba nutrūko. Rodyk jį atskirai ir **neapdorok**.
  Tada sustok ir lauk.
- Apdoroti pradedi tik tada, kai autorius aiškiai pasako („OK, perleidžiam video001“ / „perleidžiam visus“). Jei nepasako, kuriuos, paklausk.
  Paleidęs duok eigos nuorodą: `http://127.0.0.1:8766/eiga?t=<serveris/token.txt>`.
- Eiga, kai autorius leido (visada per `.venv\Scripts\python.exe`):
  1. `src\run.py sessions\videoNNN` → [1]–[3]. Sustoja: parodyk autoriui `work\contact_page*.jpg` ir lauk patvirtinimo.
  2. `src\run.py sessions\videoNNN --stage evidence` → [4], `work\evidence.md`, `work\blocks\`, `work\pages_export\`.
  3. [5]: `src\progress.py sessions\videoNNN start analysis`. Tada atskiras subagentas (švarus kontekstas) skaito `evidence.md`, lapus ir crop'us ir **grąžina** analizės tekstą su tiksliomis antraštėmis `## 1.`–`## 5.`. Pabaigus: `src\progress.py sessions\videoNNN finish analysis`.
     - Subagentas failų **nerašo**: Claude Code neleidžia subagentams kurti `.md` failų (patvirtinta 2026-10-27). Jo prompt'e nurodyk grąžinti visą `analysis.md` tekstą atsakyme. Pagrindinė sesija jį įrašo į `work\analysis.md` **nepakeistą**.
  4. `src\run.py sessions\videoNNN --stage export` → [6] `concept.md` + `pages\`, tada katalogas pervadinamas į `videoNNN_processed`.
- Jei kuris nors žingsnis nepavyko, **nepervadink**: katalogas lieka „neapdorotas“, o `work/` kešas leidžia tęsti.
  Jei pervadinti nepavyksta (Windows užrakino failą, pvz. atidarytas Explorer'yje ar grotuve), pranešk autoriui, o ne tylėk.
- Pakartotinis apdorojimas (`_processed` katalogo) tik autoriui aiškiai paprašius. Pavadinimas lieka su `_processed`.

## Git
- **`main`** = bendras įrankio branduolys (tai, ką gauna kiekvienas, kas klonuoja). Į jį rašoma **tik per Pull Request**, kurį patvirtina repo savininkas (`.github/CODEOWNERS`). Tiesiai į `main` necommit'ink.
- **`personal/<vardas>`** = kiekvieno naudotojo asmeninis branch'as: `ASMENINIS.md`, slenksčių pakeitimai savo įrašams. Asmeninis žodynas – `asmeninis/vocab.txt` (ne git'e). Jis periodiškai atsinaujina iš `main` (`git merge main`). Atgal į `main` jis **nesuliejamas**.
- Branduolio pataisymas (kodas, bendri slenksčiai, dokumentacija): `feature/<trumpas-aprasymas>` nuo `main` → PR į `main`. Jei pataisymas jau padarytas asmeniniame branch'e, perkelk jį `git cherry-pick` į feature branch'ą. `ASMENINIS.md` į PR nedėk.
- Šis srautas šiam repo pakeičia bendrą `feature/` → `staging` → `master`.
- Commit žinutės angliškai, liepiamąja nuosaka.

## Darbo taisyklės Claude Code
- Pirma testuok su **trumpu klipu** (1–2 min), tik tada leisk pilną įrašą.
- Slenksčius kalibruok pagal realų įrašą. Kalibracijos rezultatus įrašyk į `config.yaml` (jei bendri) arba `config.local.yaml` (jei tik šiam kompiuteriui) su komentaru, iš kur jie gauti.
- Po [3] žingsnio sugeneruok **debug peržiūrą**: kontaktinį lapą (contact sheet) su įvykių bbox ir timestamp'ais. Autorius ją peržiūri akimis prieš analizę.
- Whisper ir kadrų analizė vyksta lygiagrečiai, nes nepriklauso viena nuo kitos.
- Ilgi procesai: rezultatus kešuok sesijos `work/`, kad pakartotinis paleidimas nekartotų whisper'io.
- **Progresas: visi žingsniai praneša per `src/progress.py`** (`work/progress.json`). Autorius eigą mato puslapyje `/eiga` (serveris).
  - Kiekvienas žingsnis: `start(key, total, unit)` → `update(done, note)` → `finish`. Iš kešo paimtas žingsnis yra `skip`. Klaidos atveju kviesk `fail(...)` su aiškia priežastimi.
  - `total` turi būti matuojamas realiais vienetais: whisper'iui garso sekundės (`segment.end`), kadrams kadrų skaičius. Kitaip ETA bus beprasmis.
  - `note` yra trumpa gyva žinutė: whisper'iui paskutinio segmento tekstas, kadrams įvykių skaičius.
  - Analizės subagento žingsnis (`analysis`) žymimas per CLI: `python src/progress.py <sesija> start analysis` / `finish analysis`.
  - Po kalibravimo `PHASES` svorius atnaujink pagal faktines trukmes (`started`/`finished` progress.json) ir įrašyk, iš kur jie gauti.
