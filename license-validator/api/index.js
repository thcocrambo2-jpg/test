// Vercel entry point.
//
// Vercel runs each request as a serverless function, so nothing listens on
// a port here — vercel.json rewrites every path to this module and the
// Express app handles it. Indexes are not created on this path: run
// `npm run init-db` once against the same cluster (creating indexes on
// every cold start would add latency to the call that gates app startup).

export { default } from "../src/app.js";
