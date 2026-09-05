/**
 * Runtime configuration.
 *
 * Everything the service needs comes from the environment with a working default, so a
 * developer can `npm start` against a local database without a .env file and get the
 * same code paths production runs.
 */
const int = (raw, fallback) => {
  const n = Number.parseInt(String(raw ?? ""), 10);
  return Number.isFinite(n) ? n : fallback;
};

export const config = {
  port: int(process.env.PORT, 3000),
  nodeEnv: process.env.NODE_ENV ?? "production",

  // Postgres. DATABASE_URL wins; the discrete variables exist because the platform
  // team's secret store injects them that way.
  database: {
    url:
      process.env.DATABASE_URL ??
      `postgresql://${process.env.PGUSER ?? "storefront"}:${process.env.PGPASSWORD ?? "storefront"}` +
        `@${process.env.PGHOST ?? "127.0.0.1"}:${int(process.env.PGPORT, 5432)}/` +
        `${process.env.PGDATABASE ?? "storefront"}`,
    poolSize: int(process.env.PGPOOL_MAX, 12),
  },

  // Content generation. Two deployments of the same release must not look alike in the
  // catalogue, the customer list or the copy: the marketing site, the demo estate and
  // the training estate all run this image with a different value here.
  deploySeed: process.env.DEPLOY_SEED ?? "gs-1",

  // Public origin, used for absolute URLs in the sitemap, e-mail and the CORS policy.
  publicOrigin: process.env.PUBLIC_ORIGIN ?? "",

  session: {
    cookie: "sid",
    ttlSeconds: int(process.env.SESSION_TTL_SECONDS, 60 * 60 * 12),
    stepUpSeconds: int(process.env.SESSION_STEP_UP_SECONDS, 60 * 10),
  },

  // Directory the loyalty provider's rotating HMAC keys are synchronised into.
  loyaltyKeyDir: process.env.LOYALTY_KEY_DIR ?? "/var/lib/storefront/keys",

  // Where the storefront's own uploads land.
  mediaDir: process.env.MEDIA_DIR ?? "/var/lib/storefront/media",

  // Outbound fetch budget for the importer and the avatar fetcher.
  egressTimeoutMs: int(process.env.EGRESS_TIMEOUT_MS, 5000),
};

export default config;
