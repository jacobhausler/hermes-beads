// jsx-capture.mjs — minimal react/jsx-runtime stand-in for node --test.
// jsx()/jsxs() build plain element records; walk() flattens trees so tests
// can assert structure without a DOM. This module is the same instance the
// component imports through the mapped specifier (main-thread evaluation).
export const Fragment = "fragment";

let lastTree = null;

export function jsx(type, props, key) {
  const el = { type, props: props ?? {}, key: key ?? null };
  lastTree = el;
  return el;
}
export const jsxs = jsx;

export function last() { return lastTree; }

export function* walk(node) {
  if (Array.isArray(node)) { for (const n of node) yield* walk(n); return; }
  if (node && typeof node === "object" && "type" in node && "props" in node) {
    yield node;
    if (typeof node.type === "function") {
      yield* walk(node.type(node.props)); // render function components
    } else {
      yield* walk(node.props.children);
    }
    return;
  }
  if (node != null) yield node; // primitive child
}
