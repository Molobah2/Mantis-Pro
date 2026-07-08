import { build } from "esbuild";
import { mkdirSync } from "fs";

mkdirSync("../dist", { recursive: true });

await build({
  entryPoints: ["connect.tsx"],
  bundle: true,
  format: "iife",
  outfile: "../dist/connect.bundle.js",
  minify: true,
  define: {
    "process.env.NODE_ENV": '"production"',
  },
  jsx: "automatic",
  platform: "browser",
  target: ["chrome90", "firefox88", "safari14"],
  // Suppress the annoying "use client" directive warnings from React libs
  logOverride: { "ignored-bare-import": "silent" },
});

console.log("[connect-bundle] built → wallet-helper/dist/connect.bundle.js");
