/**
 * Tailwind config for the proofread web UI.
 *
 * Replaces the previous browser-side Play CDN (static/vendor/tailwind.js).
 * After editing any template class, regenerate the CSS:
 *   bash scripts/build_tailwind.sh
 * The generated file (static/vendor/tailwind.min.css) is committed so the
 * Python-only CI and the PyInstaller desktop bundle need no Node toolchain.
 */
module.exports = {
  content: ["./kzocr/proofread/templates/**/*.html"],
  theme: {
    extend: {
      colors: {
        tcm: {
          primary: "oklch(0.42 0.11 158)",
          "primary-light": "oklch(0.72 0.14 65)",
          sidebar: "oklch(0.2 0.02 155)",
          "sidebar-hover": "oklch(0.26 0.03 155)",
          bg: "oklch(0.985 0.008 85)",
          card: "oklch(0.995 0.005 85)",
          destructive: "oklch(0.55 0.20 28)",
        },
      },
    },
  },
  // Classes assembled at runtime in review.html JS (e.g. status.className =
  // 'text-green-600'). The scanner sees the literal tokens, but safelist them
  // to be safe against future refactors that build them dynamically.
  safelist: [
    "text-green-600",
    "text-red-600",
    "border-l-amber-400",
    "border-l-green-500",
  ],
  plugins: [],
};
