// The bot's command handlers: what a customer typing /start, /plans,
// /buy, /mykeys, /renew or /help gets back.
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
// ── What a tap means ───────────────────────────────────────────────────
//
// The customer picks a tier; what that *is* depends on what they already
// hold, and they are told which before they pay:
//
//   no licence          → new       a key is created
//   same tier           → renew     time is added to what is left
//   a different tier    → upgrade   the plan changes, the term restarts
//
// The licence considered is the newest one that is not revoked. A revoked
// licence is treated as no licence, so the bot cannot quietly reinstate
// somebody who was cut off — renewLicense() reactivates by design, and
// that decision belongs to a person, not to a payment.
//
// ── The import rule ────────────────────────────────────────────────────
//
// This file may read the catalogue, read licences and open orders. It must
// not write a licence: provision.js is the only thing that does that, and
// on this path it is reached only from payments.js, after money.

import { collections } from "../db.js";
import { KREA2_NODE_TAG, ORDER_MAX_OPEN_PER_USER } from "../config.js";
import { allFeatures, isFeatureEnabled, sortByRegistry } from "../features.js";
import { countOpenOrders, createOrder, markInvoiced } from "../orders.js";
import { BASE_CYCLE, allPlans, billingCycles, starsPrice } from "../plans.js";

import { answerCallbackQuery, botId, sendInvoice, sendMessage } from "./bot.js";
import { INTENT } from "./payments.js";
import * as copy from "./copy.js";

/** The prefix on a "buy this tier" button's callback data. */
const BUY_PREFIX = "buy:";

/**
 * Note that we have seen this person.
 *
 * An upsert keyed on the Telegram user id itself, so there is no second
 * identifier to keep in step and no create-or-update branch. Deliberately
 * thin — a name, a language, and whether the bot has been blocked. Nothing
 * on the licensing path reads it; it exists so that a support conversation
 * can start from "who is this?" without joining orders.
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
      cycle: price.cycle,
      popular: plan.is_popular === true,
      features: sortByRegistry(
        (plan.features || []).filter((key) => enabled.has(key)),
        order,
      ).map((key) => enabled.get(key)?.name || key),
    }));
}

/**
 * The newest licence this customer holds that has not been revoked.
 *
 * `telegram_user_id` is a label written by provision.js on keys sold here;
 * a CLI-issued key has none and is invisible to this, which is correct —
 * the bot should not offer to renew a licence it cannot describe the deal
 * for.
 */
async function currentLicense(telegramUserId) {
  const { licenses } = await collections();
  return licenses.findOne(
    { telegram_user_id: telegramUserId, active: { $ne: false } },
    { sort: { created_at: -1 } },
  );
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

/** The tier buttons, one per row so the price is never truncated. */
function buyKeyboard(offered, held) {
  return {
    inline_keyboard: offered.map((plan) => [
      {
        text:
          `${plan.name} — ${plan.price} ⭐` +
          (held && held.plan_id === plan.id ? "  (renew)" : ""),
        callback_data: `${BUY_PREFIX}${plan.id}`,
      },
    ]),
  };
}

async function replyBuy(chatId, userId) {
  const offered = await purchasablePlans();
  if (!offered.length) return sendMessage(chatId, copy.PLANS_EMPTY);

  const held = await currentLicense(userId);
  // The tier's display name, not its id: "Creator", never "creator".
  const heldName = held
    ? offered.find((row) => row.id === held.plan_id)?.name || held.plan_id
    : null;
  const text = held
    ? copy.buyPromptExisting(heldName, held.expires_at)
    : copy.BUY_PROMPT;
  return sendMessage(chatId, text, { reply_markup: buyKeyboard(offered, held) });
}

/**
 * Turn a tapped tier into an invoice.
 *
 * Everything that can refuse the sale is checked *before* the order is
 * written, and the last of those checks is that this deployment knows its
 * own node tag: half of what the customer needs to run the app comes from
 * that variable, so selling without it would take money for something that
 * cannot start. Failing here costs a tap; failing after the charge costs a
 * refund.
 */
async function startPurchase({ chatId, userId, planId }) {
  if (!KREA2_NODE_TAG) {
    console.error("tg warn   refusing to sell: KREA2_NODE_TAG is not set");
    return sendMessage(chatId, copy.CANNOT_SELL);
  }

  const offered = await purchasablePlans();
  const plan = offered.find((row) => row.id === planId);
  // Not on the list means not for sale — an old button, or a tier that
  // lost its price since the message was sent.
  if (!plan) return sendMessage(chatId, copy.PLANS_EMPTY);

  const open = await countOpenOrders(userId);
  if (open >= ORDER_MAX_OPEN_PER_USER) {
    console.warn(`tg cap    ${userId} has ${open} open orders`);
    return sendMessage(chatId, copy.TOO_MANY_OPEN);
  }

  const held = await currentLicense(userId);
  let intent = INTENT.NEW;
  if (held) intent = held.plan_id === plan.id ? INTENT.RENEW : INTENT.UPGRADE;

  const order = await createOrder({
    telegram_user_id: userId,
    telegram_chat_id: chatId,
    intent,
    plan_id: plan.id,
    cycle: plan.cycle,
    months: plan.months,
    amount_stars: plan.price,
    seats: held && intent !== INTENT.NEW ? held.seats ?? 1 : 1,
    license_key: intent === INTENT.NEW ? null : held.key,
  });

  await sendInvoice({
    chatId,
    title: copy.invoiceTitle(plan.name),
    description: copy.invoiceDescription({
      planName: plan.name,
      months: plan.months,
      renewal: intent === INTENT.RENEW,
    }),
    // The order id *is* the payload: Telegram echoes it back verbatim on
    // both pre_checkout_query and successful_payment, so the payment path
    // never has to guess what was bought.
    payload: order._id,
    amount: plan.price,
  });

  return markInvoiced(order._id);
}

async function replyMyKeys(chatId, userId) {
  const { licenses } = await collections();
  const held = await licenses
    .find({ telegram_user_id: userId })
    .sort({ created_at: -1 })
    .toArray();
  if (!held.length) return sendMessage(chatId, copy.NO_KEYS);

  const plans = await allPlans();
  const body = held
    .map((license) =>
      copy.keyBlock({
        key: license.key,
        planName: plans.get(license.plan_id)?.name || license.plan_id || "Krea 2",
        seats: license.seats ?? 1,
        expiresAt: license.expires_at,
        active: license.active !== false,
        nodeTag: KREA2_NODE_TAG,
      }),
    )
    .join("\n\n");
  return sendMessage(chatId, body);
}

/** /renew is the same purchase as tapping the tier already held. */
async function replyRenew(chatId, userId) {
  const held = await currentLicense(userId);
  if (!held) return sendMessage(chatId, copy.RENEW_NO_KEY);
  return startPurchase({ chatId, userId, planId: held.plan_id });
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

  // Never answer ourselves. Telegram posts service messages into the chat
  // on the bot's behalf — a refund notice is one — and they arrive as
  // ordinary updates whose sender is the bot. Falling through to the
  // command switch would greet a customer who has just been refunded with
  // "I only understand a few commands", and would file the bot in
  // telegram_users as though it were a customer.
  if (message.from?.id && message.from.id === botId()) {
    console.log(`tg self   ${message.from.id}  ignored`);
    return;
  }

  const userId = message.from?.id ?? "?";
  const command = parseCommand(message.text);
  console.log(
    `tg msg    ${String(userId).padEnd(12)} ${command ? `/${command}` : "(text)"}`,
  );

  try {
    await rememberUser(message.from);

    switch (command) {
      case "start":
        return await sendMessage(chatId, copy.START);
      case "plans":
        return await replyPlans(chatId);
      case "buy":
        return await replyBuy(chatId, userId);
      case "mykeys":
        return await replyMyKeys(chatId, userId);
      case "renew":
        return await replyRenew(chatId, userId);
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
    console.error(
      `tg fail   ${userId} ${command ? `/${command}` : "(text)"}:`,
      err.message,
    );
    // Best effort. If this send fails too, the router still answers 200.
    try {
      await sendMessage(chatId, copy.ERROR);
    } catch {
      /* nothing further to try */
    }
  }
}

/**
 * Handle a tapped button.
 *
 * The spinner on the customer's button runs until answerCallbackQuery is
 * called, so that happens first and unconditionally — before the work,
 * which involves a Bot API round trip of its own and might fail.
 */
export async function handleCallbackQuery(query) {
  const chatId = query?.message?.chat?.id;
  const userId = query?.from?.id;
  const data = String(query?.data || "");
  console.log(`tg tap    ${String(userId).padEnd(12)} ${data}`);

  try {
    await answerCallbackQuery(query.id);
  } catch (err) {
    // A stale query id (older than about a minute) cannot be answered.
    // Not a reason to skip the purchase the customer asked for.
    console.warn(`tg tap    could not acknowledge — ${err.message}`);
  }

  if (!chatId || !userId) return;

  try {
    await rememberUser(query.from);
    if (data.startsWith(BUY_PREFIX)) {
      return await startPurchase({
        chatId,
        userId,
        planId: data.slice(BUY_PREFIX.length),
      });
    }
    console.log(`tg tap    unknown callback data`);
  } catch (err) {
    if (err?.blocked) {
      console.log(`tg blocked ${userId}`);
      return;
    }
    console.error(`tg fail   ${userId} ${data}:`, err.message);
    try {
      await sendMessage(chatId, copy.ERROR);
    } catch {
      /* nothing further to try */
    }
  }
}
