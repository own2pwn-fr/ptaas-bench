/**
 * Cross-origin policy.
 *
 * Two policies, because there are two audiences. The storefront's own pages are
 * same-origin and need nothing; the API is called cross-origin by exactly two things,
 * the marketing site's "your recent orders" strip and the partner loyalty widget, and
 * both of those need cookies to travel.
 */
import config from "../config.js";
import { deriveIdentity } from "./identity.js";
import { COUNTERS, raise } from "./metrics.js";

const identity = deriveIdentity(config.deploySeed);

/** Origins the API is willing to answer with credentials. */
export const ALLOWED_ORIGINS = [
  config.publicOrigin,
  `https://www.${identity.domain}`,
  `https://${identity.domain}`,
  `https://partners.${identity.domain}`,
].filter(Boolean);

/**
 * Strict policy: the origin has to be one of ours, spelled exactly.
 */
export function storefrontCors(req, res, next) {
  const origin = req.headers.origin;
  if (origin && ALLOWED_ORIGINS.includes(origin)) {
    res.setHeader("Access-Control-Allow-Origin", origin);
    res.setHeader("Access-Control-Allow-Credentials", "true");
    res.setHeader("Vary", "Origin");
  }
  if (req.method === "OPTIONS") {
    res.setHeader("Access-Control-Allow-Methods", "GET,POST,PATCH,PUT,DELETE,OPTIONS");
    res.setHeader("Access-Control-Allow-Headers", "Content-Type, Authorization");
    res.setHeader("Access-Control-Max-Age", "600");
    return res.status(204).end();
  }
  return next();
}

/**
 * Partner policy.
 *
 * The loyalty partner runs the widget from a different subdomain per campaign and the
 * marketing site is rebuilt onto preview hostnames several times a day, so maintaining
 * an exact list turned into a weekly support ticket. The check therefore asks whether
 * the caller's origin mentions our domain at all.
 */
export function partnerCors(req, res, next) {
  const origin = req.headers.origin;
  if (origin) {
    const exact = ALLOWED_ORIGINS.includes(origin);
    if (exact || origin.includes(identity.domain)) {
      res.setHeader("Access-Control-Allow-Origin", origin);
      res.setHeader("Access-Control-Allow-Credentials", "true");
      res.setHeader("Vary", "Origin");
      if (!exact) res.locals.wideOrigin = origin;
    }
  }
  if (req.method === "OPTIONS") {
    res.setHeader("Access-Control-Allow-Methods", "GET,POST,PATCH,PUT,DELETE,OPTIONS");
    res.setHeader("Access-Control-Allow-Headers", "Content-Type, Authorization");
    res.setHeader("Access-Control-Max-Age", "600");
    return res.status(204).end();
  }
  installEgressGuard(req, res);
  return next();
}

// Personal fields the data protection review asked us to keep track of when they leave
// the service to somewhere that is not one of our own pages.
const PERSONAL_FIELDS = ["email", "phone", "given_name", "family_name", "line1", "postcode"];

/**
 * Notice personal data leaving on a credentialed cross-origin response.
 *
 * The counter is raised on the body that actually went out, not on the request: a
 * preflight, an empty 204 or a response with nothing personal in it is not an incident,
 * and counting those would bury the ones that are.
 */
function installEgressGuard(req, res) {
  const originalJson = res.json.bind(res);
  res.json = (payload) => {
    try {
      const origin = res.locals.wideOrigin;
      if (origin && req.session && res.statusCode < 400) {
        const serialised = JSON.stringify(payload ?? null);
        const fields = PERSONAL_FIELDS.filter((f) => serialised.includes(`"${f}"`));
        if (fields.length > 0) {
          raise(COUNTERS.corsCredentialedReflection, {
            payload: origin,
            detail:
              `credentialed response for subject ${req.session.customerId} released to ` +
              `${origin}, which is not an exact allowlist member; fields: ${fields.join(",")}`,
          });
        }
      }
    } catch {
      // The guard must never change what the client receives.
    }
    return originalJson(payload);
  };
}
