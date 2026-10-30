/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        cobalt: { DEFAULT: "#1F3A93", deep: "#162B6E", soft: "#3A57B8" },
        tile: "#E7EDF9",
        paper: "#FBFCFE",
        ink: { DEFAULT: "#18202F", muted: "#5A6478", faint: "#8A93A6" },
        line: "#D9DFEA",
        settled: { DEFAULT: "#17795A", soft: "#E3F3EC" },
        held: { DEFAULT: "#A86A12", soft: "#FBF0DC" },
        failed: { DEFAULT: "#B42318", soft: "#FCE8E6" },
      },
      fontFamily: {
        sans: ['"Schibsted Grotesk"', "ui-sans-serif", "system-ui", "sans-serif"],
      },
      fontSize: {
        // A modular scale (ratio 1.25) from a 16px body.
        xs: ["0.8rem", { lineHeight: "1.4" }],
        sm: ["0.875rem", { lineHeight: "1.45" }],
        base: ["1rem", { lineHeight: "1.55" }],
        lg: ["1.25rem", { lineHeight: "1.4" }],
        xl: ["1.563rem", { lineHeight: "1.25" }],
        "2xl": ["1.953rem", { lineHeight: "1.15" }],
        "3xl": ["2.441rem", { lineHeight: "1.08" }],
      },
    },
  },
  plugins: [],
};
