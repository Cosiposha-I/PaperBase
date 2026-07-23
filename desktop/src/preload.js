"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("paperbaseSetup", {
  getInstallLocation: () => ipcRenderer.invoke("setup:get-install-location"),
  chooseInstallLocation: () => ipcRenderer.invoke("setup:choose-install-location"),
  start: () => ipcRenderer.invoke("setup:start"),
  chooseFolder: () => ipcRenderer.invoke("setup:choose-folder"),
  applyCorpus: (corpusPath) => ipcRenderer.invoke("setup:apply-corpus", corpusPath),
  finish: () => ipcRenderer.invoke("setup:finish"),
  onProgress: (callback) => {
    ipcRenderer.on("setup:progress", (_event, data) => callback(data));
  },
});
