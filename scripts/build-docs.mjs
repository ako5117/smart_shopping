// Builds the static docs site into dist/ for Cloudflare (run by `wrangler deploy` via wrangler.jsonc).
// No dependencies: Markdown is copied as-is and rendered in the browser by site/index.html.
import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const out = join(root, "dist");

// Order here is the order of the sidebar.
const sections = [
  ["Overview", ["README.md", "CONTRIBUTING.md"]],
  ["Design", [
    "docs/roadmap.md",
    "docs/architecture.md",
    "docs/data-model.md",
    "docs/sensor-logic.md",
    "docs/reconciliation.md",
  ]],
  ["Hardware", ["hardware/bench-prototype.md", "hardware/firmware/shelf_node/README.md"]],
  ["Services", [
    "services/payments/README.md",
    "services/inventory/README.md",
    "services/catalog/README.md",
    "services/shelf/README.md",
  ]],
  ["Apps and demo", [
    "apps/checkout/README.md",
    "tools/demo/README.md",
    "tools/mpesa_simulator/README.md",
  ]],
];

rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });

const manifest = [];
for (const [title, paths] of sections) {
  const pages = [];
  for (const path of paths) {
    const src = join(root, path);
    if (!existsSync(src)) {
      console.warn(`skipping missing ${path}`);
      continue;
    }
    const text = readFileSync(src, "utf8");
    const heading = text.match(/^#\s+(.+)$/m);
    pages.push({ path, title: heading ? heading[1].trim() : path });
    mkdirSync(dirname(join(out, path)), { recursive: true });
    cpSync(src, join(out, path));
  }
  if (pages.length) manifest.push({ title, pages });
}

writeFileSync(join(out, "manifest.json"), JSON.stringify(manifest, null, 2));
cpSync(join(root, "site", "index.html"), join(out, "index.html"));
console.log(`docs site built: ${manifest.reduce((n, s) => n + s.pages.length, 0)} pages in dist/`);
