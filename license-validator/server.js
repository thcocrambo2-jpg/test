// Local / container entry point. Vercel uses api/index.js instead.

import app from "./src/app.js";
import { ensureIndexes } from "./src/db.js";
import { PORT } from "./src/config.js";

ensureIndexes()
  .then(() => console.log("indexes ready"))
  .catch((err) => console.error("could not create indexes:", err.message));

app.listen(PORT, () => {
  console.log(`license-validator listening on http://127.0.0.1:${PORT}`);
});
