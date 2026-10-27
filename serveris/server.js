// Video įkėlimo serveris – leidžia telefonu (per naršyklę, vidiniame tinkle) įkelti nufilmuotą
// koncepcijos video. Kiekvienas video gauna savo katalogą: <projektas>/sessions/video001/video.mp4.
// Į tą patį katalogą galima įkelti ir lapų nuotraukas (page1.jpg, page2.jpg ...).
// Be jokių npm priklausomybių – tik Node.js.
//
// Video siunčiamas dalimis (CHUNK_BYTES), nes 15 min įrašas yra 1–4 GB: nutrūkus Wi-Fi ar užmigus
// telefonui, įkėlimas tęsiamas nuo paskutinės gautos dalies, o ne nuo pradžių.
//
// Apsauga (keli sluoksniai):
//   1. Windows ugniasienė leidžia prievadą tik "Private" tinkle ir tik iš LocalSubnet (žr. idiegti.ps1).
//   2. Pats serveris atmeta bet kokį užklausimą ne iš privataus IP diapazono.
//   3. Kiekvienam užklausimui reikia slapto rakto (token), kuris yra adrese, kurį išsisaugai telefone.

'use strict';

const http = require('http');
const fs = require('fs');
const path = require('path');
const os = require('os');
const crypto = require('crypto');

const PORT = 8766; // pakeitus – pakeisti ir idiegti.ps1 $port
const MAX_VIDEO_BYTES = 12 * 1024 ** 3; // 12 GB – užtenka ir 15 min 4K įrašui
const CHUNK_BYTES = 16 * 1024 ** 2;     // turi sutapti su index.html
const MAX_PHOTO_BYTES = 40 * 1024 ** 2;
const DISK_RESERVE_BYTES = 3 * 1024 ** 3; // palikti vietos audio.wav, kadrams ir whisper modeliui

const TOOL_DIR = __dirname;
const PROJECT_DIR = path.resolve(TOOL_DIR, '..');
const SESSIONS_DIR = path.join(PROJECT_DIR, 'sessions');
const TOKEN_FILE = path.join(TOOL_DIR, 'token.txt');
const LOG_FILE = path.join(TOOL_DIR, 'serveris.log');

const VIDEO_EXTS = ['mp4', 'mov', 'm4v', '3gp', 'webm', 'mkv'];
const VIDEO_EXT_BY_MIME = { 'video/mp4': 'mp4', 'video/quicktime': 'mov', 'video/3gpp': '3gp', 'video/webm': 'webm', 'video/x-matroska': 'mkv' };
const PHOTO_EXT_BY_MIME = { 'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp', 'image/heic': 'heic', 'image/heif': 'heif' };

// Sesijos ID tikrinamas griežtai – jis eina į failo kelią, todėl jokio "..\" ar kitų simbolių.
// Apdorotas įrašas pervadinamas į videoNNN_processed (tai daro pipeline'as, ne serveris).
const SESSION_RE = /^video(\d{3,})(_processed)?$/;

function log(msg) {
  const line = `${new Date().toISOString()} ${msg}\n`;
  process.stdout.write(line);
  try { fs.appendFileSync(LOG_FILE, line); } catch { /* log neprivalomas */ }
}

// Raktas sugeneruojamas vieną kartą ir saugomas faile, kad telefone išsaugotas adresas veiktų ir po perkrovimo.
function loadToken() {
  if (fs.existsSync(TOKEN_FILE)) {
    const t = fs.readFileSync(TOKEN_FILE, 'utf8').trim();
    if (t) return t;
  }
  const t = crypto.randomBytes(12).toString('hex');
  fs.writeFileSync(TOKEN_FILE, t);
  return t;
}
const TOKEN = loadToken();

function tokenOk(given) {
  if (typeof given !== 'string') return false;
  const a = Buffer.from(given);
  const b = Buffer.from(TOKEN);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

// Leidžiami tik loopback ir privatūs (RFC 1918) adresai. IPv4 per IPv6 ateina kaip "::ffff:192.168.x.x".
function isPrivateAddress(addr) {
  if (!addr) return false;
  const ip = addr.startsWith('::ffff:') ? addr.slice(7) : addr;
  if (ip === '::1' || ip.startsWith('127.')) return true;
  if (ip.startsWith('10.') || ip.startsWith('192.168.')) return true;
  const m = /^172\.(\d+)\./.exec(ip);
  return !!m && +m[1] >= 16 && +m[1] <= 31;
}

function localStamp(d = new Date()) {
  return d.toLocaleString('sv-SE'); // "YYYY-MM-DD HH:MM:SS" vietiniu laiku
}

function send(res, status, body, type = 'text/plain; charset=utf-8') {
  res.writeHead(status, { 'Content-Type': type, 'Cache-Control': 'no-store' });
  res.end(body);
}
const sendJson = (res, status, obj) => send(res, status, JSON.stringify(obj), 'application/json; charset=utf-8');

// Visą užklausos turinį surenka į Buffer, bet ne daugiau nei limit (kitaip – 413).
function readBody(req, limit) {
  return new Promise((resolve, reject) => {
    const parts = [];
    let size = 0;
    req.on('data', chunk => {
      size += chunk.length;
      if (size > limit) {
        reject(Object.assign(new Error('Per didelis'), { status: 413 }));
        req.destroy();
        return;
      }
      parts.push(chunk);
    });
    req.on('end', () => resolve(Buffer.concat(parts)));
    req.on('error', reject);
  });
}

// ---------- Sesijos (sessions/videoNNN) ----------

function sessionDir(id) {
  if (!SESSION_RE.test(id || '')) return null;
  const dir = path.join(SESSIONS_DIR, id);
  return fs.existsSync(dir) ? dir : null;
}

function readInfo(dir) {
  try { return JSON.parse(fs.readFileSync(path.join(dir, 'info.json'), 'utf8')); } catch { return {}; }
}
function writeInfo(dir, info) {
  fs.writeFileSync(path.join(dir, 'info.json'), JSON.stringify(info, null, 2));
}

// Naujas katalogas = didžiausias esamas numeris + 1. mkdirSync be recursive yra atomiškas:
// jei katalogas jau yra (EEXIST), imamas kitas numeris – du vienu metu įkeliami video nesusimaišys.
// Skaičiuojami ir *_processed katalogai, kitaip po video001 → video001_processed vėl būtų sukurtas video001.
function claimNewSession() {
  fs.mkdirSync(SESSIONS_DIR, { recursive: true });
  const nums = fs.readdirSync(SESSIONS_DIR)
    .map(n => SESSION_RE.exec(n))
    .filter(Boolean)
    .map(m => +m[1]);
  let n = nums.length ? Math.max(...nums) + 1 : 1;
  for (;;) {
    const id = 'video' + String(n).padStart(3, '0');
    try {
      fs.mkdirSync(path.join(SESSIONS_DIR, id));
      return id;
    } catch (e) {
      if (e.code !== 'EEXIST') throw e;
      n++;
    }
  }
}

function partFile(dir, info) {
  return path.join(dir, `video.${info.ext}.part`);
}
function receivedBytes(dir, info) {
  try { return fs.statSync(partFile(dir, info)).size; } catch { return 0; }
}

function listSessions() {
  if (!fs.existsSync(SESSIONS_DIR)) return [];
  return fs.readdirSync(SESSIONS_DIR)
    .filter(n => SESSION_RE.test(n))
    .sort()
    .reverse()
    .map(id => {
      const dir = path.join(SESSIONS_DIR, id);
      const info = readInfo(dir);
      const files = fs.readdirSync(dir);
      return {
        id,
        original_name: info.original_name || null,
        size: info.size || 0,
        received: info.finished ? info.size : (info.ext ? receivedBytes(dir, info) : 0),
        complete: !!info.finished,
        started: info.started || null,
        photos: files.filter(f => /^page\d+\./.test(f)).length,
        processed: id.endsWith('_processed'),
      };
    });
}

// ---------- API ----------

// 1 žingsnis: telefonas praneša failo vardą ir dydį, serveris sukuria videoNNN katalogą.
function handleStart(res, url, remote) {
  const name = (url.searchParams.get('name') || '').slice(0, 200);
  const size = Number(url.searchParams.get('size'));
  const mime = (url.searchParams.get('type') || '').toLowerCase();
  if (!Number.isSafeInteger(size) || size <= 0) return send(res, 400, 'Netinkamas dydis');
  if (size > MAX_VIDEO_BYTES) return send(res, 413, `Failas per didelis (max ${MAX_VIDEO_BYTES / 1024 ** 3} GB)`);

  const free = fs.statfsSync(PROJECT_DIR);
  const freeBytes = free.bavail * free.bsize;
  if (size + DISK_RESERVE_BYTES > freeBytes) {
    return send(res, 507, `Kompiuteryje per mažai vietos: laisva ${(freeBytes / 1024 ** 3).toFixed(1)} GB`);
  }

  const nameExt = path.extname(name).slice(1).toLowerCase();
  const ext = VIDEO_EXTS.includes(nameExt) ? nameExt : (VIDEO_EXT_BY_MIME[mime] || 'mp4');

  const id = claimNewSession();
  const dir = path.join(SESSIONS_DIR, id);
  const info = { original_name: name, size, ext, uploaded_from: remote, started: localStamp(), finished: null };
  writeInfo(dir, info);
  fs.writeFileSync(partFile(dir, info), '');
  log(`Pradėtas ${id}: "${name}" ${(size / 1024 ** 2).toFixed(0)} MB iš ${remote}`);
  sendJson(res, 200, { id, received: 0, chunk: CHUNK_BYTES });
}

function handleStatus(res, url) {
  const id = url.searchParams.get('id');
  const dir = sessionDir(id);
  if (!dir) return send(res, 404, 'Nėra tokios sesijos');
  const info = readInfo(dir);
  sendJson(res, 200, { id, size: info.size, received: info.finished ? info.size : receivedBytes(dir, info), complete: !!info.finished });
}

// Vienu metu į tą pačią sesiją rašo tik viena užklausa (jei telefonas pakartotų užklausą, kol ankstesnė dar vyksta).
const busy = new Set();

// 2 žingsnis: dalis prirašoma tik tada, jei offset == jau gautų baitų skaičius.
// Kitaip grąžinama 409 su tikru gautų baitų skaičiumi – telefonas tęsia nuo ten.
async function handleChunk(req, res, url) {
  const id = url.searchParams.get('id');
  const dir = sessionDir(id);
  if (!dir) return send(res, 404, 'Nėra tokios sesijos');
  const info = readInfo(dir);
  if (info.finished) return sendJson(res, 409, { received: info.size, complete: true });
  if (busy.has(id)) return sendJson(res, 409, { received: receivedBytes(dir, info), busy: true });

  busy.add(id);
  try {
    const offset = Number(url.searchParams.get('offset'));
    const have = receivedBytes(dir, info);
    if (offset !== have) {
      req.resume(); // išmesti turinį, kad ryšys užsidarytų tvarkingai
      return sendJson(res, 409, { received: have });
    }
    const body = await readBody(req, CHUNK_BYTES);
    if (have + body.length > info.size) return send(res, 400, 'Daugiau duomenų nei deklaruota');
    await fs.promises.appendFile(partFile(dir, info), body);
    sendJson(res, 200, { received: have + body.length });
  } catch (e) {
    if (!res.headersSent) send(res, e.status || 500, e.message);
  } finally {
    busy.delete(id);
  }
}

// 3 žingsnis: visi baitai gauti → video.<ext>.part pervadinamas į video.<ext>.
// Iki tol pipeline'as failo nemato, todėl pusiau įkelto video niekas neanalizuos.
function handleFinish(res, url) {
  const id = url.searchParams.get('id');
  const dir = sessionDir(id);
  if (!dir) return send(res, 404, 'Nėra tokios sesijos');
  const info = readInfo(dir);
  if (info.finished) return sendJson(res, 200, { id, file: `sessions/${id}/video.${info.ext}` });

  const have = receivedBytes(dir, info);
  if (have !== info.size) return sendJson(res, 409, { received: have });

  fs.renameSync(partFile(dir, info), path.join(dir, `video.${info.ext}`));
  info.finished = localStamp();
  writeInfo(dir, info);
  log(`Baigtas ${id}: video.${info.ext} (${(info.size / 1024 ** 2).toFixed(0)} MB)`);
  sendJson(res, 200, { id, file: `sessions/${id}/video.${info.ext}` });
}

// Lapų nuotraukos: page1.jpg, page2.jpg ... (CLAUDE.md: jos turi prioritetą prieš video kadrus).
async function handlePhoto(req, res, url, remote) {
  const id = url.searchParams.get('id');
  const dir = sessionDir(id);
  if (!dir) return send(res, 404, 'Nėra tokios sesijos');
  const mime = (req.headers['content-type'] || '').split(';')[0].trim().toLowerCase();
  const ext = PHOTO_EXT_BY_MIME[mime];
  if (!ext) return send(res, 415, `Netinkamas failo tipas: ${mime}`);

  try {
    const body = await readBody(req, MAX_PHOTO_BYTES);
    if (!body.length) return send(res, 400, 'Tuščias failas');
    // Numeris parenkamas tik gavus visą failą, o 'wx' neleidžia perrašyti jau esamo.
    for (let n = 1; ; n++) {
      if (fs.readdirSync(dir).some(f => f.startsWith(`page${n}.`))) continue;
      const name = `page${n}.${ext}`;
      try {
        fs.writeFileSync(path.join(dir, name), body, { flag: 'wx' });
      } catch (e) {
        if (e.code === 'EEXIST') continue;
        throw e;
      }
      log(`Nuotrauka ${id}/${name} (${Math.round(body.length / 1024)} KB) iš ${remote}`);
      return sendJson(res, 200, { file: `sessions/${id}/${name}` });
    }
  } catch (e) {
    if (!res.headersSent) send(res, e.status || 500, e.message);
  }
}

// ---------- Apdorojimo eiga (rašo src/progress.py → work/progress.json) ----------

// process.kill(pid, 0) nieko nežudo – tik patikrina, ar procesas egzistuoja (veikia ir Windows).
// EPERM reiškia, kad procesas yra, bet priklauso kitam naudotojui.
function pidAlive(pid) {
  try { process.kill(pid, 0); return true; } catch (e) { return e.code === 'EPERM'; }
}

// Eigos sąraše rodomi ir pagalbiniai katalogai (video001_calib – kalibravimo ištrauka), nors įkėlimo sąraše
// ir numeracijoje jų nėra (ten – tik SESSION_RE).
const PROGRESS_RE = /^video\d{3,}(_[a-z]+)?$/;

function listProgress() {
  if (!fs.existsSync(SESSIONS_DIR)) return [];
  const out = [];
  for (const id of fs.readdirSync(SESSIONS_DIR).filter(n => PROGRESS_RE.test(n))) {
    const file = path.join(SESSIONS_DIR, id, 'work', 'progress.json');
    if (!fs.existsSync(file)) continue;
    let p;
    try { p = JSON.parse(fs.readFileSync(file, 'utf8')); } catch { continue; } // retas atvejis: pervadinamas tą akimirką
    p.id = id; // katalogo vardas gali būti jau su _processed
    // Vykdomas žingsnis su pid, bet procesas nebegyvas → pipeline'as nulūžo arba buvo nutrauktas.
    for (const ph of p.phases || []) {
      if (ph.status === 'running' && ph.pid) ph.alive = pidAlive(ph.pid);
    }
    out.push(p);
  }
  return out.sort((a, b) => (b.updated || 0) - (a.updated || 0)).slice(0, 10);
}

const PAGE = fs.readFileSync(path.join(TOOL_DIR, 'index.html'), 'utf8').replace('__CHUNK__', String(CHUNK_BYTES));
const EIGA_PAGE = fs.readFileSync(path.join(TOOL_DIR, 'eiga.html'), 'utf8');

const server = http.createServer((req, res) => {
  const remote = req.socket.remoteAddress;
  if (!isPrivateAddress(remote)) {
    log(`Atmesta: ne vidinio tinklo adresas ${remote}`);
    req.destroy();
    return;
  }

  const url = new URL(req.url, 'http://localhost');
  const token = url.searchParams.get('t') || req.headers['x-token'];
  if (!tokenOk(token)) return send(res, 403, 'Neteisingas arba trūkstamas raktas (t=...)');

  try {
    const route = `${req.method} ${url.pathname}`;
    if (route === 'GET /') return send(res, 200, PAGE, 'text/html; charset=utf-8');
    if (route === 'GET /api/sesijos') return sendJson(res, 200, listSessions());
    if (route === 'GET /eiga') return send(res, 200, EIGA_PAGE, 'text/html; charset=utf-8');
    // now – serverio laikas sekundėmis, kad telefono laikrodžio paklaida nesugadintų ETA skaičiavimo.
    if (route === 'GET /api/eiga') return sendJson(res, 200, { now: Date.now() / 1000, runs: listProgress() });
    if (route === 'POST /api/video/pradeti') return handleStart(res, url, remote);
    if (route === 'GET /api/video/busena') return handleStatus(res, url);
    if (route === 'PUT /api/video/dalis') return handleChunk(req, res, url);
    if (route === 'POST /api/video/baigti') return handleFinish(res, url);
    if (route === 'POST /api/nuotrauka') return handlePhoto(req, res, url, remote);
    send(res, 404, 'Nerasta');
  } catch (e) {
    log(`Klaida ${req.method} ${url.pathname}: ${e.message}`);
    if (!res.headersSent) send(res, 500, 'Serverio klaida');
  }
});

// Ilgas įkėlimas lėtame Wi-Fi: Node numatytas requestTimeout (5 min) vienai 16 MB daliai užtenka,
// bet išjungiam, kad lėtas telefonas nebūtų nukirstas viduryje dalies.
server.requestTimeout = 0;

server.listen(PORT, '0.0.0.0', () => {
  const lanIps = Object.values(os.networkInterfaces()).flat()
    .filter(i => i && i.family === 'IPv4' && !i.internal && !i.address.startsWith('169.254.'))
    .map(i => i.address);
  log(`Serveris veikia. Video keliauja į ${SESSIONS_DIR}`);
  for (const ip of lanIps) log(`Atidaryk telefone: http://${ip}:${PORT}/?t=${TOKEN}`);
  log(`Apdorojimo eiga šiame kompiuteryje: http://127.0.0.1:${PORT}/eiga?t=${TOKEN}`);
  log(`Arba (jei telefonas palaiko .local): http://${os.hostname().toLowerCase()}.local:${PORT}/?t=${TOKEN}`);
});
