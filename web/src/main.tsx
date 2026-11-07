import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { CloudChat } from "./cloud/CloudChat";
import { markDesktopShell } from "./desktop";
import { applyLang } from "./i18n";
import { StoreProvider } from "./store";
import { applyTheme } from "./theme";
import "./index.css";

applyLang();
applyTheme();
markDesktopShell();
// ?cloud=1 opens the relay-backed chat: the first slice of the single client, served from
// the cloud origin while the runtime screens keep their own transport.
const cloudMode = new URLSearchParams(location.search).has("cloud");

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {cloudMode ? (
      <CloudChat />
    ) : (
      <StoreProvider>
        <App />
      </StoreProvider>
    )}
  </React.StrictMode>,
);
