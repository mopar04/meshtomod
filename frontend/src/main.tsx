import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import DonatePrompt from "./DonatePrompt";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
    <DonatePrompt />
  </StrictMode>,
);
