// Test-only module-resolution hook: a relative `./x.js` specifier resolves
// to `./x.ts` when only the TypeScript file exists. See ts-resolve.js.
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

export async function resolve(specifier, context, nextResolve) {
  const relative = specifier.startsWith("./") || specifier.startsWith("../");
  if (relative && specifier.endsWith(".js") && context.parentURL) {
    const candidate = new URL(`${specifier.slice(0, -3)}.ts`, context.parentURL);
    if (existsSync(fileURLToPath(candidate))) {
      return nextResolve(candidate.href, context);
    }
  }
  return nextResolve(specifier, context);
}
