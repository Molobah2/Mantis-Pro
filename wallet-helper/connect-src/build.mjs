import { build } from "esbuild";
import { mkdirSync } from "fs";

mkdirSync("../dist", { recursive: true });

const sharedOptions = {
  bundle: true,
  format: "iife",
  minify: true,
  define: {
    "process.env.NODE_ENV": '"production"',
  },
  jsx: "automatic",
  platform: "browser",
  target: ["chrome90", "firefox88", "safari14"],
  // Suppress the annoying "use client" directive warnings from React libs
  logOverride: { "ignored-bare-import": "silent" },
};

await build({
  ...sharedOptions,
  entryPoints: ["connect.tsx"],
  outfile: "../dist/connect.bundle.js",
});
console.log("[connect-bundle] built → wallet-helper/dist/connect.bundle.js");

await build({
  ...sharedOptions,
  entryPoints: ["eth-connect.tsx"],
  outfile: "../dist/eth-connect.bundle.js",
});
console.log("[connect-bundle] built → wallet-helper/dist/eth-connect.bundle.js");
