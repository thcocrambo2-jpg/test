// The bot's command handlers: what a customer typing /start, /plans or
// /help gets back.
//
// ── Where the prices come from ─────────────────────────────────────────
//
// From `plans` in Mongo, through allPlans() and starsPrice(), on every
// request. Not from a constant here, and not from /v1/plans over HTTP.
//
// One catalogue, read the same way the pricing page reads it, is what stops
// the bot quoting a price the invoice does not charge — and it means a
// price change in Atlas reaches the bot within the plans cache's minute,
// with no redeploy. It also matters that `price_stars_monthly` is
// deliberately *not* on /v1/plans: that endpoint's projection is fixed, so
// there is nothing to fetch there even if we wanted to.
//
// ── What is for sale, and how a tier stays off the shelf ───────────────
//
// A plan is offered here only if it is public **and** carries a usable
// Stars price. Both, not either: `is_public: false` is what keeps a tier
// off the pricing page, and an absent Stars price is what keeps it out of
// an invoice, and the four internal tiers (`admin`, `admin-minimal`,
// `test-krea1-only` and the hand-made `customer-admin`, which exists only
// in Atlas) rely on the second. Requiring both means adding a Stars price
// to an internal tier by mistake still does not put it on sale.
//
// ── The import rule ────────────────────────────────────────────────────
//
// This file may read the catalogue and send messages. It must not write a
// licence: provision.js is the only thing that does that, and nothing in
// this phase has any business calling it.

import { collections } from "../db.js";
import { allFeatures, isFeatureEnabled, sortByRegistry } from "../features.js";
import { BASE_CYCLE, allPlans, billingCycles, starsPrice } from "../plans.js";

import { sendMessage } from "./bot.js";
import * as copy from "./copy.js";

/**
 * Note that we have seen this person.
 *
 * An upsert keyed on the Telegram user id itself, so there is no second
 * identifier to keep in step and no create-or-update branch. Deliberately
 * thin — a name, a language, and later whether the bot has been blocked.
 * Nothing on the licensing path reads it; it exists so that a support
 * conversation can start from "who is this?" without joining orders.
 *
 * Failing to record a user must never cost them their answer, so this is
 * called for its effect and its error is swallowed by the caller's own
 * handler rather than aborting the command.
 */
async function rememberUser(from) {
  if (!from?.id) return;
  const now = new Date();
  const { telegram_users } = await collections();
  await telegram_users.updateOne(
    { _id: from.id },
    {
      $set: {
        username: from.username ?? null,
        first_name: from.first_name ?? null,
        language_code: from.language_code ?? null,
        last_seen_at: now,
      },
      $setOnInsert: { first_seen_at: now, is_blocked: false },
    },
    { upsert: true },
  );
}

/**
 * The command name from a message, lowercased and without the @botname
 * suffix Telegram appends in groups. Null for anything that is not a
 * command.
 */
function parseCommand(text) {
  const first = String(text || "").trim().split(/\s+/)[0] || "";
  if (!first.startsWith("/")) return null;
  return first.slice(1).split("@")[0].toLowerCase();
}

/**
 * Everything on sale, priced, in display order.
 *
 * Exported because it is the answer to "what would the bot show right now?"
 * — which is worth being able to ask from a script without a chat id.
 */
export async function purchasablePlans() {
  const [plans, catalogue, cycles] = await Promise.all([
    allPlans(),
    allFeatures(),
    billingCycles(),
  ]);
  // The base cycle is always present and always enabled — normalizeCycles()
  // guarantees it, because a catalogue with no monthly price has no price
  // at all. Only monthly is offered here; quarterly and yearly exist in the
  // billing document but are switched off, and turning one on is a change
  // to the buy flow, not to this listing.
  const monthly = cycles.find((cycle) => cycle.id === BASE_CYCLE);

  // Same treatment as /v1/plans: a feature the catalogue has disabled is
  // dropped from the list shown, while the plan still grants it to everyone
  // already on that tier. The tab stops being advertised; nobody loses it.
  const enabled = new Map(
    [...catalogue].filter(([, row]) => isFeatureEnabled(row)),
  );
  const order = [...enabled.keys()];

  return [...plans.values()]
    .filter((plan) => plan.is_public !== false)
    .map((plan) => ({ plan, price: starsPrice(plan, monthly) }))
    .filter(({ price }) => price !== null)
    .sort((a, b) => (a.plan.sort_order ?? 0) - (b.plan.sort_order ?? 0))
    .map(({ plan, price }) => ({
      id: plan._id,
      name: plan.name || plan._id,
      description: plan.description || null,
      price: price.total,
      months: price.months,
      popular: plan.is_popular === true,
      features: sortByRegistry(
        (plan.features || []).filter((key) => enabled.has(key)),
        order,
      ).map((key) => enabled.get(key)?.name || key),
    }));
}

async function replyPlans(chatId) {
  const offered = await purchasablePlans();
  if (!offered.length) {
    // Reached only if every plan lost its Stars price, which would be an
    // ops accident rather than a state the code can produce. Say something
    // a customer can act on and let the log carry the alarm.
    console.warn("tg warn   no plan carries a stars price");
    return sendMessage(chatId, copy.PLANS_EMPTY);
  }
  const body = [
    copy.PLANS_HEADER,
    "",
    offered.map((plan) => copy.planBlock(plan)).join("\n\n"),
    "",
    copy.PLANS_FOOTER,
  ].join("\n");
  return sendMessage(chatId, body);
}

/**
 * Handle one incoming message.
 *
 * Every failure is caught here rather than thrown at the router: the
 * router's job is to answer Telegram 200 whatever happens, and a customer
 * whose /plans threw should be told to try again rather than left with
 * silence. The reason goes to the log, never to them.
 *
 * Note what is logged — the user id and the command, never the message
 * text and never a name. The update body is customer content.
 */
export async function handleMessage(message) {
  const chatId = message?.chat?.id;
  if (!chatId) return;

  const userId = message.from?.id ?? "?";
  const command = parseCommand(message.text);
  console.log(`tg msg    ${String(userId).padEnd(12)} ${command ? `/${command}` : "(text)"}`);

  try {
    await rememberUser(message.from);

    switch (command) {
      case "start":
        return await sendMessage(chatId, copy.START);
      case "plans":
        return await replyPlans(chatId);
      case "help":
        return await sendMessage(chatId, copy.HELP);
      default:
        return await sendMessage(chatId, copy.FALLBACK);
    }
  } catch (err) {
    // A blocked customer is not an error worth alarming about: they closed
    // the conversation, and there is nothing to retry or fix.
    if (err?.blocked) {
      console.log(`tg blocked ${userId}`);
      return;
    }
    console.error(`tg fail   ${userId} ${command ? `/${command}` : "(text)"}:`, err.message);
    // Best effort. If this send fails too, the router still answers 200.
    try {
      await sendMessage(chatId, copy.ERROR);
    } catch {
      /* nothing further to try */
    }
  }
}
