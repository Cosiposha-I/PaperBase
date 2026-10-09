// Самопроверка собранного приложения — запускается после electron-builder.
//
// Ловит то, что не видно на машине разработчика: в сборку попал не тот код.
//   1. каждый файл пакета paperbase в сборке побайтно равен исходнику;
//   2. метка сборки на месте и её версия совпадает с package.json;
//   3. встроенный Python запускается и компилирует упакованный код без ошибок;
//   4. установщик существует под постоянным именем (на него ведёт ссылка в README).
// Любое расхождение — код возврата 1, сборка останавливается.
"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { execFileSync } = require("child_process");

const desktop = path.join(__dirname, "..");
const dist = path.join(desktop, "dist");
const pkg = require("../package.json");
const problems = [];
const ok = (msg) => console.log("  ok   " + msg);
const bad = (msg) => { problems.push(msg); console.log("  СБОЙ " + msg); };

function resourcesDir() {
  if (process.platform === "darwin") {
    const macDir = fs.readdirSync(dist).find((d) => d.startsWith("mac") &&
      fs.existsSync(path.join(dist, d, "PaperBase.app")));
    return macDir ? path.join(dist, macDir, "PaperBase.app", "Contents", "Resources") : null;
  }
  return path.join(dist, "win-unpacked", "resources");
}

function walk(dir, base = dir, acc = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    if (e.name === "__pycache__") continue;
    const full = path.join(dir, e.name);
    if (e.isDirectory()) walk(full, base, acc);
    else acc.push(path.relative(base, full));
  }
  return acc;
}

const sha = (f) => crypto.createHash("sha256").update(fs.readFileSync(f)).digest("hex");

const res = resourcesDir();
if (!res || !fs.existsSync(res)) {
  console.error("не найдена распакованная сборка в " + dist);
  process.exit(1);
}
console.log("проверяю сборку: " + res);

// 1. код в сборке = исходники
const srcPkg = path.join(desktop, "..", "paperbase");
const dstPkg = path.join(res, "app", "paperbase");
const srcFiles = walk(srcPkg);
let diff = 0;
for (const rel of srcFiles) {
  const d = path.join(dstPkg, rel);
  if (!fs.existsSync(d)) { diff++; bad("нет в сборке: paperbase/" + rel); }
  else if (sha(d) !== sha(path.join(srcPkg, rel))) { diff++; bad("отличается от исходника: paperbase/" + rel); }
}
if (!diff) ok("код paperbase в сборке совпадает с исходниками (" + srcFiles.length + " файлов)");

for (const f of ["app/requirements.txt", "app/config.example.toml"]) {
  if (fs.existsSync(path.join(res, f))) ok("на месте: " + f); else bad("нет файла: " + f);
}

// 2. метка сборки
const infoPath = path.join(res, "build-info.json");
if (!fs.existsSync(infoPath)) bad("нет build-info.json");
else {
  const info = JSON.parse(fs.readFileSync(infoPath, "utf8"));
  if (info.version !== pkg.version) bad("версия в метке " + info.version + " != " + pkg.version);
  else ok("метка сборки: " + info.version + ", коммит " + info.commit + (info.dirty ? " (+правки)" : "") + ", " + info.built);
  const want = (process.env.GITHUB_SHA || "").slice(0, 7);
  if (want && info.commit !== want) bad("коммит в метке " + info.commit + " != собираемому " + want);
}

// 3. встроенный Python жив и код компилируется
const py = process.platform === "win32"
  ? path.join(res, "pyapp", "python.exe")
  : path.join(res, "pyapp", "bin", "python3");
if (!fs.existsSync(py)) bad("нет встроенного Python: " + py);
else {
  try {
    const ver = execFileSync(py, ["--version"]).toString().trim();
    // -B и отдельная папка для .pyc: проверка не должна оставлять следов в сборке
    const tmp = fs.mkdtempSync(path.join(require("os").tmpdir(), "pb-verify-"));
    execFileSync(py, ["-B", "-X", "pycache_prefix=" + tmp, "-m", "compileall", "-q", dstPkg], { stdio: "pipe" });
    fs.rmSync(tmp, { recursive: true, force: true });
    ok("встроенный " + ver + " компилирует упакованный код");
  } catch (e) {
    bad("встроенный Python не справился: " + String(e.stderr || e.message).slice(0, 300));
  }
}

// 4. установщик под постоянным именем
const installer = process.platform === "darwin" ? "PaperBase.dmg" : "PaperBase-Setup.exe";
const instPath = path.join(dist, installer);
if (!fs.existsSync(instPath)) bad("нет установщика " + installer);
else ok("установщик " + installer + ", " + Math.round(fs.statSync(instPath).size / 1048576) + " МБ");

if (problems.length) {
  console.error("\nСамопроверка сборки НЕ пройдена: " + problems.length + " проблем(ы).");
  process.exit(1);
}
console.log("\nСамопроверка сборки пройдена.");
