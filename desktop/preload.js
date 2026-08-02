const { contextBridge, ipcRenderer } = require('electron');

// Expose safe desktop APIs to the renderer process (React UI)
contextBridge.exposeInMainWorld('electronAPI', {
  platform: process.platform,
  isElectron: true,
  onThemeChanged: (callback) => ipcRenderer.on('theme-changed', (event, theme) => callback(theme)),
  sendNotification: (title, body) => ipcRenderer.send('show-notification', { title, body }),
});
