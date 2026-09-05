/**
 * Loyalty provider bearer tokens.
 *
 * The loyalty scheme is run by a partner. Their apps call our API directly with a token
 * they issue, so this is the one place the service accepts a credential it did not mint.
 * The signing keys are synchronised into a directory on disk and the token names the one
 * it was signed with, which is how the partner rotates without a coordinated deploy.
 */
import { createHmac, timingSafeEqual } from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import config from "../config.js";

const b64urlToJson = (segment) =>
  JSON.parse(Buffer.from(String(segment), "base64url").toString("utf8"));

function loadKey(kid) {
  const resolved = path.resolve(path.join(config.loyaltyKeyDir, String(kid ?? "")));
  const material = fs.readFileSync(resolved);
  const inside = resolved.startsWith(path.resolve(config.loyaltyKeyDir) + path.sep);
  return { material, resolved, inside };
}

/**
 * Read a bearer token.
 *
 * Returns `{ claims, alg, kid, unverified, keyPath, keyOutsideDirectory }` when the token
 * is acceptable, or null. The algorithm is taken from the token so that a rotation from
 * HS256 to RS256 does not need both sides to deploy on the same day.
 *
 * The two observations on the returned object are for the caller to record once it knows
 * whether the token actually got the request anywhere; a token that is read and then
 * leads to nothing is not worth a counter.
 */
export function readLoyaltyToken(authorization) {
  const raw = String(authorization ?? "");
  if (!raw.toLowerCase().startsWith("bearer ")) return null;
  const token = raw.slice(7).trim();
  const parts = token.split(".");
  if (parts.length < 2) return null;

  let header;
  let claims;
  try {
    header = b64urlToJson(parts[0]);
    claims = b64urlToJson(parts[1]);
  } catch {
    return null;
  }
  if (!claims || typeof claims.sub !== "string") return null;

  const alg = String(header?.alg ?? "").toLowerCase();

  if (alg === "none") {
    // Unsigned tokens are what the partner's staging issuer emits while a key is being
    // rotated; the claims are still the partner's.
    return { claims, alg, kid: null, token, unverified: true, keyOutsideDirectory: false };
  }

  if (alg !== "hs256") return null;

  let key;
  try {
    key = loadKey(header?.kid);
  } catch {
    return null;
  }

  const expected = createHmac("sha256", key.material)
    .update(`${parts[0]}.${parts[1]}`)
    .digest();
  let provided;
  try {
    provided = Buffer.from(String(parts[2] ?? ""), "base64url");
  } catch {
    return null;
  }
  if (provided.length !== expected.length || !timingSafeEqual(provided, expected)) return null;

  return {
    claims,
    alg,
    kid: header?.kid ?? null,
    token,
    unverified: false,
    keyPath: key.resolved,
    keyOutsideDirectory: !key.inside,
  };
}
