/**
 * Password hashing and session lookup.
 *
 * scrypt from the standard library rather than a dependency: the parameters below are
 * the ones the platform team signed off on, and keeping the implementation in-tree means
 * a Node upgrade cannot silently change them.
 */
import { randomBytes, scrypt, timingSafeEqual } from "node:crypto";
import { promisify } from "node:util";

const scryptAsync = promisify(scrypt);

const N = 16384;
const KEYLEN = 32;

export async function hashPassword(password, salt = randomBytes(16).toString("hex")) {
  const derived = await scryptAsync(String(password), salt, KEYLEN, { N, r: 8, p: 1 });
  return { hash: derived.toString("hex"), salt };
}

export async function verifyPassword(password, hash, salt) {
  try {
    const derived = await scryptAsync(String(password), salt, KEYLEN, { N, r: 8, p: 1 });
    const stored = Buffer.from(String(hash), "hex");
    if (stored.length !== derived.length) return false;
    return timingSafeEqual(stored, derived);
  } catch {
    return false;
  }
}

export const newSessionId = () => randomBytes(24).toString("base64url");
