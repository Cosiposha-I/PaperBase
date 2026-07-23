// Запуск Python-бэкенда (paperbase serve) и ожидание готовности API.
"use strict";

const { spawn } = require("child_process");
const http = require("http");
const net = require("net");
const { runtimePython, pyappSourceDir } = require("./paths");

function findFreePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

// 3 минуты: сервер грузит модель эмбеддингов синхронно до открытия порта (server.py
// ServerState.__init__), а холодная загрузка ~2 ГБ модели (диск + перенос на GPU/CPU)
// может занимать значительно больше 30 с — на этом мы уже словили ложный таймаут.
function waitForServer(port, timeoutMs = 180000) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const tryOnce = () => {
      const req = http.get(`http://127.0.0.1:${port}/api/stats`, (res) => {
        res.resume();
        resolve();
      });
      req.on("error", () => {
        if (Date.now() - started > timeoutMs) reject(new Error("сервер не ответил вовремя"));
        else setTimeout(tryOnce, 400);
      });
    };
    tryOnce();
  });
}

/** Запускает `python -m paperbase serve --port N` и возвращает {port, child}. */
async function startServer(env) {
  const port = await findFreePort();
  const child = spawn(runtimePython(), ["-m", "paperbase", "serve", "--port", String(port)], {
    cwd: pyappSourceDir(),
    env,
  });
  child.stdout.on("data", (d) => process.stdout.write(`[paperbase] ${d}`));
  child.stderr.on("data", (d) => process.stderr.write(`[paperbase] ${d}`));
  await waitForServer(port);
  return { port, child };
}

module.exports = { startServer, findFreePort, waitForServer };
