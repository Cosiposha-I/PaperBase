// Первый запуск: venv, torch (CUDA/CPU/MPS), зависимости, модель bge-m3, проверка.
// Каждый шаг сообщает прогресс через onProgress(stage, percent, message) — это то,
// что видит пользователь на экране setup.html. Ошибки — человеческим языком,
// без трейсбеков (детали пишутся в лог).
"use strict";

const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");
const {
  portablePythonPath, runtimeDir, runtimePython, pyappSourceDir,
  setupDoneMarker, installRoot,
} = require("./paths");

// Большие файлы (torch ~2.5 ГБ, модель ~2.2 ГБ) по нестабильному интернету могут
// прерываться посреди скачивания дольше стандартного таймаута pip (15 с) — поэтому
// увеличиваем таймаут и число повторов. Полностью от обрывов это не защищает —
// пользователь может нажать «Попробовать снова» (venv и уже скачанные файлы не теряются).
const PIP_NETWORK_ARGS = ["--timeout", "100", "--retries", "10"];

function run(cmd, args, { onLine, cwd, env } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, { cwd, env: env || process.env });
    let lastErr = "";
    const feed = (buf) => {
      const text = buf.toString("utf8");
      lastErr = (lastErr + text).slice(-4000);
      if (onLine) text.split(/\r?\n/).forEach((l) => l.trim() && onLine(l));
    };
    child.stdout.on("data", feed);
    child.stderr.on("data", feed);
    child.on("error", (e) => reject(new Error(`не удалось запустить ${cmd}: ${e.message}`)));
    child.on("close", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`${cmd} завершился с ошибкой (код ${code}). Подробности: ${lastErr.slice(-300)}`));
    });
  });
}

function findBootstrapPython() {
  const portable = portablePythonPath();
  if (portable) return { exe: portable, isSystem: false };
  // Только для разработки: в собранном приложении портативный Python обязателен.
  const candidates = process.platform === "win32" ? ["python", "py"] : ["python3", "python"];
  for (const c of candidates) {
    try {
      require("child_process").execFileSync(c, ["--version"], { stdio: "ignore" });
      return { exe: c, isSystem: true };
    } catch { /* пробуем следующий */ }
  }
  throw new Error("Python не найден. В официальной сборке он встроен — похоже, эта копия "
    + "приложения собрана без него, либо это запуск в разработке без Python на PATH.");
}

function hasNvidiaGpu() {
  if (process.platform !== "win32" && process.platform !== "linux") return false;
  try {
    require("child_process").execFileSync("nvidia-smi", { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}

async function ensureVenv(onProgress) {
  if (fs.existsSync(runtimePython())) return;
  onProgress("venv", 5, "Готовлю окружение Python…");
  const { exe } = findBootstrapPython();
  fs.mkdirSync(path.dirname(runtimeDir()), { recursive: true });
  await run(exe, ["-m", "venv", runtimeDir()]);
}

async function installTorch(onProgress) {
  onProgress("torch", 15, "Устанавливаю PyTorch…");
  const py = runtimePython();
  // --upgrade обязателен: без него pip видит «torch уже установлен» (в любой версии,
  // не важно из какого индекса) и ничего не делает — поймано на этой же машине при
  // переустановке с cu121 на cu128.
  const args = ["-m", "pip", "install", "--upgrade", ...PIP_NETWORK_ARGS, "torch"];
  if (process.platform === "win32" && hasNvidiaGpu()) {
    onProgress("torch", 15, "Найдена видеокарта NVIDIA — ставлю GPU-версию PyTorch…");
    // cu128, а не более старый cu121: тот даёт torch 2.5.x, который (а) не знает
    // новейшие GPU (Blackwell/RTX 50xx, sm_120 — проверено именно на этой машине)
    // и (б) слишком стар для загрузки .bin-весов некоторых моделей (torch>=2.6,
    // см. CVE-2025-32434) — без этого сборка молча откатывается на слабую fallback-модель.
    args.push("--index-url", "https://download.pytorch.org/whl/cu128");
  } else if (process.platform === "darwin") {
    onProgress("torch", 15, "Ставлю PyTorch (на Apple Silicon будет использован ускоритель MPS)…");
  } else {
    onProgress("torch", 15, "Видеокарта NVIDIA не найдена — ставлю CPU-версию PyTorch…");
  }
  await run(py, args, { onLine: (l) => onProgress("torch", 20, l) });
}

async function installRequirements(onProgress) {
  onProgress("deps", 35, "Устанавливаю остальные зависимости…");
  const py = runtimePython();
  const req = path.join(pyappSourceDir(), "requirements.txt");
  await run(py, ["-m", "pip", "install", ...PIP_NETWORK_ARGS, "-r", req],
    { onLine: (l) => onProgress("deps", 40, l) });
}

function parseTqdmPercent(line) {
  const m = line.match(/(\d{1,3})%\|/);
  return m ? Math.min(100, parseInt(m[1], 10)) : null;
}

async function downloadModel(onProgress, userDataModelsDir) {
  const target = path.join(userDataModelsDir, "bge-m3");
  if (fs.existsSync(path.join(target, "config.json"))) return target;
  onProgress("model", 50, "Скачиваю модель эмбеддингов bge-m3 (~2.2 ГБ)…");
  fs.mkdirSync(target, { recursive: true });
  const py = runtimePython();
  const script = `
from huggingface_hub import snapshot_download
snapshot_download(repo_id="BAAI/bge-m3", local_dir=r"${target.replace(/\\/g, "\\\\")}")
print("MODEL_DOWNLOAD_OK")
`.trim();
  try {
    await run(py, ["-c", script], {
      env: { ...process.env, HF_HUB_DOWNLOAD_TIMEOUT: "100" },
      onLine: (l) => {
        const pct = parseTqdmPercent(l);
        onProgress("model", pct != null ? 50 + Math.round(pct * 0.35) : 50,
          pct != null ? `Скачиваю модель… ${pct}%` : l);
      },
    });
  } catch (e) {
    throw new Error("Не удалось скачать модель эмбеддингов (нет интернета или "
      + "HuggingFace недоступен). Можно скачать её вручную и положить в папку:\n"
      + target + "\nПодробности: " + e.message);
  }
  return target;
}

function writeInitialConfig(configPath, modelDir) {
  if (fs.existsSync(configPath)) return;
  const toml = `[paths]
corpus_dir = ""
data_dir = "data"

[embedding]
model = "${modelDir.replace(/\\/g, "/")}"
fallback_model = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
device = "auto"
batch_size = 8

[chunking]
chunk_tokens = 800
overlap_tokens = 150
min_chars_per_page = 30

[search]
default_k = 8

[ocr]
languages = "eng+rus"
tesseract_cmd = ""

[crossref]
enabled = true
timeout = 5
mailto = ""

[backup]
enabled = true
before_ingest = true
keep = 10
min_interval_hours = 12
`;
  fs.writeFileSync(configPath, toml, "utf8");
}

async function verifyInstall(onProgress, env) {
  onProgress("verify", 90, "Проверяю установку…");
  const py = runtimePython();
  await run(py, ["-m", "paperbase", "check"], { cwd: pyappSourceDir(), env,
    onLine: (l) => onProgress("verify", 95, l) });
}

async function runFirstTimeSetup(onProgress) {
  const root = installRoot();
  const modelsDir = path.join(root, "models");
  const configPath = path.join(root, "config.toml");
  fs.mkdirSync(root, { recursive: true });

  await ensureVenv(onProgress);
  await installTorch(onProgress);
  await installRequirements(onProgress);
  const modelDir = await downloadModel(onProgress, modelsDir);
  writeInitialConfig(configPath, modelDir);

  const env = { ...process.env, PAPERBASE_CONFIG: configPath, PAPERBASE_DATA_DIR: path.join(root, "data") };
  await verifyInstall(onProgress, env);

  fs.writeFileSync(setupDoneMarker(), JSON.stringify({ done: true, at: new Date().toISOString() }));
  onProgress("done", 100, "Готово!");
  return { configPath, env };
}

module.exports = { runFirstTimeSetup, findBootstrapPython, hasNvidiaGpu };
