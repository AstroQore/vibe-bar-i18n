// Test-only. Registers the resolution hook below so `node --test` can run
// the lane straight from source.
//
// Node strips TypeScript types on its own (22.18+), but its resolver takes
// import specifiers literally: `./generated/catalog.js` does not fall back
// to `catalog.ts`. TypeScript, esbuild and Vite all do that fallback,
// which is why the source spells its imports with `.js` — that is the one
// spelling that keeps a consumer's `tsc --noEmit` green. This bridges the
// gap for our own test run and ships to nobody.
import { register } from "node:module";

register("./ts-resolve-hooks.js", import.meta.url);
