/**
 * The only bridge of the relay-hosted model picker: the page says what the person chose,
 * the shell closes the window and hands the answer to the chat. No Node, no filesystem,
 * no host API — the page's own key is short-lived and lives in the URL fragment.
 */
import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("nlPicker", {
  send: (payload: { type: string; id?: string }) => ipcRenderer.send("nanomuse:model-picker:message", payload),
});
