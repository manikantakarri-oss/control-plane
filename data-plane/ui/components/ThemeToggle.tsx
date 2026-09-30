"use client";

import { useEffect, useState } from "react";

type Choice = "light" | "dark" | "system";
const KEY = "agent-portal-theme";

/** Writes the choice onto <html>. "system" removes the attribute so the CSS
 *  media query takes over again. */
export function applyTheme(choice: Choice) {
  const root = document.documentElement;
  if (choice === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", choice);
}

function stored(): Choice {
  try {
    const v = localStorage.getItem(KEY);
    if (v === "light" || v === "dark" || v === "system") return v;
  } catch {
    // Private windows and blocked site data both throw here; system is a fine
    // answer in that case.
  }
  return "system";
}

const ORDER: Choice[] = ["light", "dark", "system"];
const LABEL: Record<Choice, string> = { light: "Light", dark: "Dark", system: "System" };
const ICON: Record<Choice, string> = { light: "☀", dark: "☾", system: "◐" };

export function ThemeToggle() {
  const [choice, setChoice] = useState<Choice>("system");

  // Read on mount rather than during render: the value only exists in the
  // browser, and this component is inside a static export.
  useEffect(() => {
    setChoice(stored());
  }, []);

  function next() {
    const pick = ORDER[(ORDER.indexOf(choice) + 1) % ORDER.length];
    setChoice(pick);
    applyTheme(pick);
    try {
      localStorage.setItem(KEY, pick);
    } catch {
      // Not being able to remember the choice is not worth an error; it just
      // resets on the next visit.
    }
  }

  return (
    <button
      type="button"
      onClick={next}
      className="btn btn-quiet"
      title={`Appearance: ${LABEL[choice]}. Click to change.`}
      aria-label={`Appearance: ${LABEL[choice]}. Click to change.`}
    >
      <span aria-hidden>{ICON[choice]}</span>
      <span className="hidden sm:inline">{LABEL[choice]}</span>
    </button>
  );
}
