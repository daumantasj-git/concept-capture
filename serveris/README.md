# Video serveris telefonui

Telefono naršyklėje atidarai puslapį, spaudi **🎥 Įkelti video** ir pasirenki įrašą iš galerijos.
Serveris pats sukuria naują katalogą `sessions/video001/` (paskui `video002/` ir t.t.), ir į jį įrašo `video.mp4` (iPhone atveju `video.mov`).
Prie kiekvieno įrašo galima pridėti lapų nuotraukas (**📷 Lapo nuotrauka**). Jos išsaugomos kaip `page1.jpg`, `page2.jpg`…

Serveris video **tik priima**, o apdorojimas pradedamas tik tada, kai paprašai Claude. Apdorotas katalogas pervadinamas į `video001_processed/`, o puslapyje rodoma „✓ apdorota“.

Prievadas **8766**. Jį keičiant, reikia pakeisti ir `server.js` `PORT`, ir `idiegti.ps1` `$port`.

## Įdiegimas (vieną kartą)

Administratoriaus PowerShell lange, repo šaknyje:

```powershell
powershell -ExecutionPolicy Bypass -File .\serveris\idiegti.ps1
```

Skriptas atspausdina adresą su raktu, pvz. `http://192.168.1.23:8766/?t=...`.
Atidaryk jį telefone ir išsisaugok: Chrome → ⋮ → **Add to Home screen**.

Pašalinti galima su `pasalinti.ps1` (taip pat admin lange).
Paleisti rankiniu būdu (be įdiegimo): `node serveris\server.js`. Tada ugniasienė gali paklausti leidimo. Leisk tik **Private**.

## Apdorojimo eiga

`http://127.0.0.1:8766/eiga?t=<raktas>` šiame kompiuteryje, telefone tas pats adresas su kompiuterio IP. Į jį veda ir nuoroda **📊 eiga** įkėlimo puslapyje.
- Rodo bendrą %, likusį laiką ir numatomą pabaigos laiką. Matosi kiekvienas žingsnis su savo progresu, trukme ir paskutine žinute (pvz. whisper'io paskutinis segmentas).
- Duomenys imami iš `sessions/videoNNN/work/progress.json`, kurį rašo `src/progress.py`.
- Likęs laikas yra **įvertinimas**: jis skaičiuojamas iš greičio iki šiol, o žingsnių svoriai paimti iš `src/progress.py` `PHASES`.
- Jei pipeline'o procesas nulūžo, rodoma **„sustojo“**. Jei 2 min negauta žinių (pvz. užmigo kompiuteris), rodoma **„vyksta?“**.

## Kaip veikia didelių failų įkėlimas

- Video siunčiamas **16 MB dalimis**. Nutrūkus Wi-Fi, puslapis pats kartoja (iki 20 kartų iš eilės).
- Jei telefonas užmigo arba uždarei naršyklę, vėl pasirink **tą patį** video. Įkėlimas tęsis tame pačiame `videoNNN`, ne iš naujo.
- Kol įkėlimas nebaigtas, failas vadinasi `video.mp4.part`. Pipeline'as jo nemato.
- `info.json` saugo originalų failo vardą, dydį ir įkėlimo laiką.
- Greitis (**patvirtinta**, 2026-10-27, 5 GHz Wi-Fi): 2,12 GB įkelta per 5 min 48 s, t.y. ~6 MB/s per 5 GHz Wi-Fi. 15–20 min įrašui skaičiuok ~6 min.

## Apsauga

- Ugniasienė įleidžia tik iš **Private** tinklo ir tik iš **vietinio potinklio**.
- Serveris pats atmeta ne privačius IP adresus.
- Be rakto (`?t=...`, saugomas `token.txt`) grąžinama 403. Norint jį pakeisti, ištrink `token.txt` ir perleisk užduotį.
- Serveris atmeta video, jei diske neliktų bent 3 GB laisvos vietos (audio.wav, kadrams, whisper modeliui).

## Jei nustojo veikti

- **Pasikeitė kompiuterio IP.** Ilgalaikis sprendimas: maršrutizatoriuje „DHCP reservation“ šiam kompiuteriui.
- Ar veikia: `Get-ScheduledTask 'Video analize serveris'`, žurnalas `serveris.log`.
- Kompiuteris turi būti įjungtas (ne miego režime) ir tame pačiame Wi-Fi kaip telefonas.
