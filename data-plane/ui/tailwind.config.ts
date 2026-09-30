import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "media",
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: { DEFAULT: "#18181b", dim: "#52525b", faint: "#71717a" },
        line: "#e4e4e7",
        surface: "#ffffff",
        canvas: "#f8f8f9",
        brand: { DEFAULT: "#2f6df6", ink: "#ffffff", soft: "#eaf0fe" },
      },
      borderRadius: { xl2: "14px" },
    },
  },
  plugins: [],
};
export default config;
