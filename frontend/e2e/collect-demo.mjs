// Copies the Playwright videos of the demo run into demo-output/ with readable names.
import { copyFileSync, existsSync, mkdirSync, readdirSync } from "node:fs";
import { basename, dirname, join } from "node:path";

const OUT = "demo-output";
mkdirSync(OUT, { recursive: true });
if (!existsSync("test-results")) {
  console.error("No test-results/ found; run `npm run demo` first.");
  process.exit(1);
}
const videos = readdirSync("test-results", { recursive: true })
  .map(String)
  .filter((p) => p.endsWith(".webm") && p.includes("demo"));
for (const rel of videos) {
  const dir = basename(dirname(join("test-results", rel)));
  const name = dir.includes("409") || dir.includes("another-tab") ? "goalpulse-stale-revision.webm" : "goalpulse-demo.webm";
  copyFileSync(join("test-results", rel), join(OUT, name));
  console.log(`video: ${OUT}/${name}`);
}
console.log(`screenshots: ${readdirSync(OUT).filter((f) => f.endsWith(".png")).length} in ${OUT}/`);
