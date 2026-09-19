import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
    "./src/**/*.{ts,tsx}",
  ],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // NBM-branded panel surfaces (dark, map-friendly UI).
        surface: {
          DEFAULT: "hsl(215 28% 10%)",
          raised: "hsl(215 28% 14%)",
          overlay: "hsl(215 28% 17%)",
        },
        accent: {
          DEFAULT: "hsl(199 89% 48%)",
          muted: "hsl(199 89% 38%)",
        },
      },
      fontFamily: {
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Consolas",
          "Liberation Mono",
          "monospace",
        ],
      },
      boxShadow: {
        panel: "0 8px 30px rgba(0, 0, 0, 0.5)",
      },
    },
  },
  plugins: [],
};

export default config;
