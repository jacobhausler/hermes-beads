// ESM loader hooks for the node test run: map the two specifiers the desktop
// plugin loader maps in the app (react/jsx-runtime) onto the capture shim so
// pure components are testable without installing React.
export async function resolve(specifier, context, next) {
  if (specifier === "react/jsx-runtime" || specifier === "react") {
    return { url: new URL("./jsx-capture.mjs", import.meta.url).href,
      shortCircuit: true };
  }
  return next(specifier, context);
}
