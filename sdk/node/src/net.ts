import { BlockList, isIP } from "node:net";

/**
 * Address matching for the synthetic-monitoring source ranges.
 *
 * Built on `net.BlockList` rather than hand-rolled arithmetic so that IPv6 prefix
 * handling is the runtime's problem and not ours.
 */
export interface SourceMatcher {
  matches(address: string | undefined): boolean;
  readonly size: number;
}

/**
 * Normalise an address as reported by a socket.
 *
 * A dual-stack listener reports IPv4 peers as `::ffff:10.0.0.4`. Comparing that form
 * against an IPv4 prefix silently never matches, so it is folded back to IPv4 first.
 */
export function normaliseAddress(address: string | undefined): string | null {
  if (!address) return null;
  let value = address.trim();
  if (value.startsWith("[") && value.endsWith("]")) value = value.slice(1, -1);
  // Strip a zone index (`fe80::1%eth0`), which no prefix ever carries.
  const zone = value.indexOf("%");
  if (zone > 0) value = value.slice(0, zone);
  const mapped = /^::ffff:(\d{1,3}(?:\.\d{1,3}){3})$/i.exec(value);
  if (mapped?.[1]) value = mapped[1];
  return isIP(value) === 0 ? null : value;
}

/**
 * Compile a list of CIDR prefixes (bare addresses accepted as host routes).
 *
 * Unparseable entries are skipped rather than thrown: a typo in a deployment variable
 * must not stop a service from starting.
 */
export function compileSourceMatcher(prefixes: readonly string[]): SourceMatcher {
  const list = new BlockList();
  let size = 0;

  for (const raw of prefixes) {
    const entry = raw.trim();
    if (!entry) continue;
    const slash = entry.lastIndexOf("/");
    const addressPart = slash < 0 ? entry : entry.slice(0, slash);
    const address = normaliseAddress(addressPart);
    if (!address) continue;
    const family = isIP(address) === 6 ? "ipv6" : "ipv4";
    const full = family === "ipv6" ? 128 : 32;
    const prefix = slash < 0 ? full : Number.parseInt(entry.slice(slash + 1), 10);
    if (!Number.isInteger(prefix) || prefix < 0 || prefix > full) continue;
    try {
      list.addSubnet(address, prefix, family);
      size += 1;
    } catch {
      // Rejected by the runtime (malformed address for its family): skip it.
    }
  }

  return {
    size,
    matches(candidate: string | undefined): boolean {
      if (size === 0) return false;
      const address = normaliseAddress(candidate);
      if (!address) return false;
      try {
        return list.check(address, isIP(address) === 6 ? "ipv6" : "ipv4");
      } catch {
        return false;
      }
    },
  };
}
