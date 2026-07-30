// One-time setup: create the indexes the service depends on.
//
//   npm run init-db
//
// Idempotent, so re-running after a schema tweak is safe. Run it against
// the same cluster the deployment points at — Vercel's cold path does not
// create indexes, because doing that per invocation would add latency to
// the call that gates a customer's app startup.

import { ensureIndexes, getDb } from "../src/db.js";
import { DB_NAME, SESSION_TTL_SECONDS, STALE_SECONDS } from "../src/config.js";

const db = await getDb();
await ensureIndexes();

console.log(`database        ${DB_NAME}`);
console.log(`collections     licenses, sessions, plans, features`);
console.log(`stale window    ${STALE_SECONDS}s (a seat frees itself after this)`);
console.log(`session TTL     ${SESSION_TTL_SECONDS}s (cleanup only)`);
console.log("\nindexes:");
// plans and features are keyed by their string _id, so they need no
// indexes beyond the one Mongo creates for _id itself.
for (const name of ["licenses", "sessions"]) {
  for (const index of await db.collection(name).indexes()) {
    console.log(`  ${name}.${index.name}`);
  }
}
console.log("\nNext: npm run seed-catalog");
console.log(
  '      npm run issue-key -- --name "Acme Corp" --plan pro --seats 2',
);
process.exit(0);
