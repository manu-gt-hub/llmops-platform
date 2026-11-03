/**
 * setup.ts — Idempotent creation of the Cloudflare resources, run locally once
 * and by the deploy workflow on every deploy:
 *
 *   - D1 database "llmops" (its id is written into wrangler.jsonc)
 *
 * The AI Gateway "default" is created by Cloudflare on the first request.
 * The admin password is a Worker secret: npx wrangler secret put ADMIN_TOKEN
 * (the deploy workflow sets it from the ADMIN_TOKEN repository secret).
 *
 *   CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN in .env (or npx wrangler login)
 *   npm run setup && npm run migrate && npm run deploy
 */

import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";

const D1_NAME = "llmops";
const CONFIG = new URL("../wrangler.jsonc", import.meta.url);

/** Run the local wrangler CLI (same behaviour on Windows, macOS, Linux and CI). */
function wrangler(args: string[]): string {
  const require = createRequire(import.meta.url);
  const pkgPath = require.resolve("wrangler/package.json");
  const bin = join(dirname(pkgPath), (require(pkgPath) as { bin: Record<string, string> }).bin.wrangler);
  const result = spawnSync(process.execPath, [bin, ...args], { encoding: "utf-8", maxBuffer: 16 * 1024 * 1024 });
  if (result.status !== 0) throw new Error(`wrangler ${args.join(" ")} failed\n${result.stderr}${result.stdout}`);
  return result.stdout;
}

if (existsSync(".env")) process.loadEnvFile(".env");

const d1Id = () =>
  (JSON.parse(wrangler(["d1", "list", "--json"])) as { name: string; uuid: string }[]).find((d) => d.name === D1_NAME)?.uuid;

let id = d1Id();
if (id) {
  console.log(`D1 database ${D1_NAME} exists (${id})`);
} else {
  wrangler(["d1", "create", D1_NAME]);
  id = d1Id();
  if (!id) throw new Error("D1 database was not created");
  console.log(`Created D1 database ${D1_NAME} (${id})`);
}

const config = readFileSync(CONFIG, "utf-8");
const updated = config.replace(/("database_id":\s*")[^"]*(")/, `$1${id}$2`);
if (updated !== config) {
  writeFileSync(CONFIG, updated);
  console.log("Wrote database_id into wrangler.jsonc");
}
console.log("Ready. Next: npm run migrate && npm run deploy");
