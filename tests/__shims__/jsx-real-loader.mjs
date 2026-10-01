// jsx-real-loader.mjs — mounted-smoke shim: the REAL-code path of
// tests/__shims__/jsx-loader.mjs. Instead of mapping react/jsx-runtime onto
// the capture shim, this hook resolves react / react-dom / scheduler / jsdom
// onto the READ-ONLY node_modules of the rich-ui plugin (never installed,
// never written), so the desktop components mount against genuine React 19.
// Everything else resolves normally; desktop/*.mjs import
// "react/jsx-runtime" and get the real jsx-runtime.
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import path from "node:path";

const RU = process.env.RICH_UI_NODE_MODULES
  || "node_modules";
const req = createRequire(pathToFileURL(path.join(RU, "hermes-rich-ui-anchor.js")).href);
const HEADS = ["react", "react-dom", "scheduler", "jsdom"];

export async function resolve(specifier, context, next) {
  const head = specifier.startsWith("@")
    ? specifier.split("/").slice(0, 2).join("/")
    : specifier.split("/")[0];
  if (HEADS.includes(head)) {
    return { url: pathToFileURL(req.resolve(specifier)).href, shortCircuit: true };
  }
  return next(specifier, context);
}
