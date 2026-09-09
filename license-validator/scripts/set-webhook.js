// Point Telegram at this deployment, or unhook it.
//
//   npm run set-webhook -- --url https://<node-tag>.vercel.app
//   npm run set-webhook -- --show                  # what Telegram thinks
//   npm run set-webhook -- --delete                # stop delivery
//   npm run set-webhook -- --url ... --drop-pending
//
// ── Why --url is required and not read from the environment ────────────
//
// Registering a webhook points a live bot at one deployment, and every
// customer message goes there until it is changed. There is exactly one of
// them per bot — no staging copy running alongside — so the target is
// typed out rather than picked up from whatever happens to be in .env. The
// cost is a few extra characters; what it buys is that a preview
// deployment cannot silently capture production traffic because a variable
// was set from an earlier experiment.
//
// ── What --drop-pending does, and why it is not the default ────────────
//
// Telegram queues updates while a webhook is unset or failing, and delivers
// the backlog on the next successful registration. Dropping that backlog
// throws away customer messages — possibly including a payment
// notification, which is the one update that must never be lost. So the
// default keeps the queue and the flag exists for the case where the
// backlog is known to be junk from testing.
//
// ── Why nothing here calls process.exit() ──────────────────────────────
//
// Every other script in this directory ends with process.exit(0), because
// a live MongoClient holds the event loop open and they would otherwise
// hang. This one talks only to the Bot API, and calling process.exit()
// while fetch's socket pool is still closing aborts the process with a
// libuv assertion on Windows and a non-zero exit code — a script that did
// its job and then reported failure to whatever ran it. Nothing here keeps
// the loop alive, so it ends on its own.
//
// Talks to the Bot API only. No database, no admin token, no HTTP call to
// the service itself.

import { TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET } from "../src/config.js";
import {
  deleteWebhook,
  getMe,
  getWebhookInfo,
  setWebhook,
} from "../src/telegram/bot.js";
import {
  ALLOWED_UPDATES,
  webhookPath,
  webhookUrl,
} from "../src/telegram/router.js";

function args(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    if (!argv[i].startsWith("--")) continue;
    const name = argv[i].slice(2);
    const next = argv[i + 1];
    if (next === undefined || next.startsWith("--")) out[name] = true;
    else {
      out[name] = next;
      i += 1;
    }
  }
  return out;
}

const opts = args(process.argv.slice(2));

const USAGE = [
  "Usage: npm run set-webhook -- --url https://<node-tag>.vercel.app",
  "       npm run set-webhook -- --show",
  "       npm run set-webhook -- --delete",
].join("\n");

/** Print what Telegram currently believes, including its last error. */
async function show() {
  const info = await getWebhookInfo();
  console.log(`url         ${info.url || "(none)"}`);
  console.log(`pending     ${info.pending_update_count ?? 0}`);
  console.log(`secret      ${info.has_custom_certificate ? "cert" : "header"}`);
  console.log(`updates     ${(info.allowed_updates || ["(all)"]).join(", ")}`);
  if (info.last_error_message) {
    // The single most useful line in this script when something is wrong:
    // Telegram reports the failure it saw, which is usually a 404 from a
    // path mismatch or a secret mismatch.
    console.log(
      `last error  ${info.last_error_message} ` +
        `(${new Date((info.last_error_date || 0) * 1000).toISOString()})`,
    );
  }
}

async function main() {
  if (!TELEGRAM_BOT_TOKEN) {
    console.error(
      "TELEGRAM_BOT_TOKEN is not set.\n" +
        "  Put it in license-validator/.env for local use, and in the Vercel\n" +
        "  project's environment variables for the deployment.",
    );
    return 1;
  }
  if (!TELEGRAM_WEBHOOK_SECRET) {
    console.error(
      "TELEGRAM_WEBHOOK_SECRET is not set.\n" +
        "  Generate one with:  openssl rand -hex 32\n" +
        "  It authenticates every update, and the webhook path is derived\n" +
        "  from it, so the same value must be set here and on the deployment.",
    );
    return 1;
  }

  // Confirms the token works and names the bot, so a token pasted from the
  // wrong chat with BotFather is caught before anything is registered.
  const me = await getMe();
  console.log(`bot         @${me.username}  (${me.id})`);

  if (opts.show) {
    await show();
    return 0;
  }

  if (opts.delete) {
    await deleteWebhook({ dropPendingUpdates: opts["drop-pending"] === true });
    console.log("webhook     deleted — Telegram will stop delivering updates");
    return 0;
  }

  if (typeof opts.url !== "string") {
    console.error(USAGE);
    return 1;
  }
  if (!opts.url.startsWith("https://")) {
    // Telegram refuses plain http outright; catching it here says why.
    console.error("--url must be https — Telegram will not deliver over http.");
    return 1;
  }

  await setWebhook({
    url: webhookUrl(opts.url),
    secretToken: TELEGRAM_WEBHOOK_SECRET,
    allowedUpdates: [...ALLOWED_UPDATES],
    dropPendingUpdates: opts["drop-pending"] === true,
  });

  // The path is printed; the secret never is. Anyone reading this terminal
  // can already see the URL in getWebhookInfo, and the header token is what
  // actually authenticates.
  console.log(`path        /tg/webhook/${webhookPath()}`);
  console.log(`updates     ${ALLOWED_UPDATES.join(", ")}`);
  console.log("");
  await show();
  console.log(
    "\nThe same TELEGRAM_WEBHOOK_SECRET must be set on the deployment, or\n" +
      "every update is rejected with a 404 and shows up above as a last error.",
  );
  return 0;
}

process.exitCode = await main();
