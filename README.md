# 🎥 concept-capture

**Nupieši ir papasakoji savo idėją. AI ją užrašo, sutvarko ir pakritikuoja.**

Nusifilmuoji telefonu, kaip ant popieriaus lapų spalvotais flomasteriais piešia koncepcijos schemą ir kartu ją pasakoji **lietuviškai**. Įrankis iš to video padaro:

- 📝 **lietuvišką transkripciją** su laiko žymomis. Ji daroma lokaliai, su whisper `large-v3-turbo`;
- ✏️ **ką, kur ir kada nupiešei**: kiekvienas piešinio gabalas susiejamas su tuo, ką tuo metu kalbėjai;
- 📄 **švarius lapų vaizdus**: ištiesintus, be šešėlių ir be rankos;
- 🧠 **AI analizę**: santrauką, ko trūksta, rizikas ir prieštaravimus, kaip supaprastinti, ir klausimus tau.

Rezultatas yra aplankas `concept.md` + `pages/`. Jį įmeti į kitą Claude Code projektą kaip koncepcijos aprašą.

---

## Ko reikia

| | |
|---|---|
| 💻 Kompiuteris | Windows 10/11, ≥ 8 GB RAM (geriau 16), ~10 GB laisvos vietos |
| 🤖 [Claude Code](https://claude.com/claude-code) | jis valdo visą procesą ir daro analizę |
| 📱 Telefonas + stovas | filmuoti iš viršaus |
| 🏠 Namų Wi-Fi | telefonas ir kompiuteris **tame pačiame tinkle**. Serveris veikia **tik namų (Private) tinkle**, kavinėje ar darbe nepasieksi |

## Įdiegimas (vieną kartą)

```powershell
git clone <šio repo adresas>
cd concept-capture        # klonuoto repo aplankas
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

`setup.ps1` įdiegia, ko trūksta: Python, ffmpeg, Node.js ir Python paketus. Tada parsiunčia whisper modelį (~1,6 GB) ir **įvertina tavo kompiuterį**: CPU, RAM, ar yra NVIDIA GPU su CUDA, ar veikia GPU video dekodavimas. Pagal tai parenka greičiausius nustatymus ir įrašo juos į `config.local.yaml`.

Tada **administratoriaus** PowerShell lange įdiek serverį telefonui:

```powershell
powershell -ExecutionPolicy Bypass -File .\serveris\idiegti.ps1
```

Jis atspausdins adresą, pvz. `http://192.168.1.23:8766/?t=...`. Atidaryk jį telefone ir išsisaugok: **Add to Home screen**.

## Filmavimas

- 📐 Telefonas **vertikaliai**, ant stovo virš stalo. Kamera per visą įrašą **nejuda**.
- 🔒 Užrakink **exposure/focus** (AE/AF lock: ilgai palaikyk pirštą ant ekrano).
- 📄 Lapas pritvirtintas ir nejuda. Visas lapas matosi kadre.
- 🖊️ Piešk spalvotais flomasteriais. **Ta pati spalva = ta pati mintis**, o kita spalva = nauja mintis.
- ✋ Stenkis rankos nelaikyti ant ką tik nupieštos vietos. Kalbėk natūraliai, pauzės netrukdo.
- 📸 Pabaigoje gali nufotografuoti kiekvieną lapą. Tos nuotraukos turi pirmenybę prieš video kadrus.

## Naudojimas

1. Telefone atidaryk išsaugotą puslapį → **🎥 Įkelti video**. 15 min įrašas įkeliamas per ~6 min. Nutrūkus įkėlimas tęsiasi.
2. Kompiuteryje, šiame aplanke, paleisk `claude` ir parašyk: **„ar yra naujų?“**
3. Claude parodys įkeltus įrašus. Parašyk: **„OK, perleidžiam video001“**.
4. Eigą matai naršyklėje (nuorodą duos Claude). Po kadrų analizės Claude parodys peržiūros lapą ir paklaus, ar viskas gerai.
5. Gausi `sessions\video001_processed\concept.md` ir `pages\`.

Apdorojimas trunka maždaug tiek, kiek pats įrašas, ar mažiau. Viskas priklauso nuo kompiuterio (įvertinimas).

## 🔐 Privatumas

- Video, garsas ir transkripcija **lieka tavo kompiuteryje**. Whisper ir kadrų analizė vyksta lokaliai.
- Į Claude (Anthropic) siunčiama tik analizei: transkripcijos tekstas, lapų vaizdai ir piešinių iškarpos.
- Serveris priima tik iš namų tinklo ir tik su slaptu raktu adrese.
- Į git niekada nepatenka: video, garsas, nuotraukos, rezultatai (`sessions/`) ir aplankas **`asmeninis/`**. Ten dėk, kas neturi būti viešinama, pvz. `asmeninis/vocab.txt` su darbo sistemų pavadinimais whisper'iui. Jis prijungiamas prie `vocab.txt` automatiškai.

## 🌿 Branch'ai: kiekvienas gali turėti savo

- **`main`** yra bendras įrankis. Į jį patenkama **tik per Pull Request**, kurį patvirtina repo savininkas.
- **Savo pakeitimams pasidaryk savo branch'ą**, pvz. `personal/jonas`. Ten laikyk `ASMENINIS.md` (kaip nori, kad Claude su tavim bendrautų, tavo filmavimo įpročiai) ir slenksčių pakeitimus:
  ```powershell
  git checkout -b personal/jonas
  git push -u origin personal/jonas     # jei turi rašymo teisę į šį repo
  ```
  Atnaujinti iš `main`: `git merge main`.
- Teisę kelti savo branch'ą į šį GitHub repo turi tik **pakviesti bendradarbiai** (collaborators). Kitu atveju pasidaryk **fork'ą** ir dirbk ten.
- Radai klaidą ar patobulinai įrankį? Pasidaryk `feature/<kas-pakeista>` nuo `main` ir atidaryk PR. Asmeninių failų į PR nedėk.
