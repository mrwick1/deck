import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { registerSW } from "virtual:pwa-register";
import App from "./App";
import "./index.css";

registerSW({ immediate: true });

// Follow the system theme.
const dark = matchMedia("(prefers-color-scheme: dark)");
const theme = () =>
  document.documentElement.classList.toggle("dark", dark.matches);
theme();
dark.addEventListener("change", theme);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
