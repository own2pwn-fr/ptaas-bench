/**
 * Session middleware.
 *
 * The session id is an opaque random string in a host-only cookie; everything about the
 * session lives in the database so that a deploy does not sign every customer out and so
 * that the sessions list in the account area is real.
 */
import config from "../config.js";
import { sql, one } from "../db.js";
import { forbidden, unauthorized } from "./errors.js";
import { newSessionId } from "./auth.js";

export async function openSession(customerId, userAgent) {
  const sid = newSessionId();
  const expires = new Date(Date.now() + config.session.ttlSeconds * 1000);
  await sql(
    `INSERT INTO sessions (sid, customer_id, created_at, expires_at, user_agent)
     VALUES ($1, $2, now(), $3, $4)`,
    [sid, customerId, expires, String(userAgent ?? "").slice(0, 256)],
  );
  return { sid, expires };
}

export async function closeSession(sid) {
  if (sid) await sql(`DELETE FROM sessions WHERE sid = $1`, [sid]);
}

export function sessionCookie(res, sid, expires) {
  res.cookie(config.session.cookie, sid, {
    httpOnly: true,
    sameSite: "lax",
    // The edge terminates TLS, so the application sets Secure from the deployment's
    // public origin rather than from its own listener.
    secure: config.publicOrigin.startsWith("https://"),
    path: "/",
    expires,
  });
}

export function clearSessionCookie(res) {
  res.clearCookie(config.session.cookie, { path: "/" });
}

/**
 * Resolve the session on every request. Never rejects: an expired or unknown cookie
 * simply means an anonymous visitor, which most of the storefront is designed for.
 */
export async function attachSession(req, _res, next) {
  try {
    const sid = req.cookies?.[config.session.cookie];
    if (sid) {
      const row = await one(
        `SELECT s.sid, s.customer_id, s.expires_at, s.step_up_until,
                c.role, c.email, c.display_name
           FROM sessions s JOIN customers c ON c.id = s.customer_id
          WHERE s.sid = $1 AND s.expires_at > now()`,
        [sid],
      );
      if (row) {
        req.session = {
          sid: row.sid,
          customerId: row.customer_id,
          role: row.role,
          email: row.email,
          displayName: row.display_name,
          stepUpUntil: row.step_up_until,
        };
        // What the telemetry middleware groups by, and what the audit log records.
        req.auth = { subject: String(row.customer_id) };
      }
    }
  } catch {
    // A database blip must not sign the whole storefront out; the request continues
    // anonymously and the pages that need a session will ask for one.
  }
  next();
}

export const currentSubject = (req) =>
  req.session ? String(req.session.customerId) : null;

export const isStaff = (req) => req.session?.role === "staff";

export function requireSession(req, _res, next) {
  if (!req.session) return next(unauthorized());
  return next();
}

export function requireStaff(req, _res, next) {
  if (!req.session) return next(unauthorized());
  if (!isStaff(req)) return next(forbidden("This area is for store staff."));
  return next();
}

export function stepUpSatisfied(req) {
  const until = req.session?.stepUpUntil;
  return Boolean(until && new Date(until).getTime() > Date.now());
}
