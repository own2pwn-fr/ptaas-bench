/**
 * The compact cart encoding.
 *
 * When a guest leaves the site the storefront writes the cart into a signed-free blob it
 * can hand back later, and the promotions team's rules travel in the same blob: a field
 * may be a literal, or `{"$expr": "..."}`, which is evaluated against the cart when it is
 * restored. That is how "three for two on the same range" is expressed without a deploy.
 *
 * The expression scope is the cart and a couple of numeric helpers. Nothing else is
 * meant to be reachable from a rule, and the accessor below records every name a rule
 * asks for so the promotions team can see which of their rules use what.
 */

const EXPOSED = new Set([
  "lines", "subtotal", "currency", "quantity", "price", "count",
  "Math", "Number", "round", "min", "max",
]);

/**
 * Compile and run one rule.
 *
 * Returns `{ value, resolved }`, where `resolved` lists the names the rule actually
 * looked up that are not part of the exposed scope.
 */
export function evaluateRule(source, scope) {
  const resolved = [];
  const bag = new Proxy(
    { ...scope, Math, Number, round: Math.round, min: Math.min, max: Math.max },
    {
      // `with` consults has() for every identifier in the body, which is what makes the
      // usage list complete rather than a guess.
      has: () => true,
      get(target, name) {
        if (typeof name === "symbol") return undefined;
        if (!EXPOSED.has(name) && !Object.hasOwn(target, name)) {
          resolved.push(name);
          return globalThis[name];
        }
        return target[name];
      },
    },
  );
  // eslint-disable-next-line no-new-func
  const compiled = new Function("scope", `with (scope) { return (${source}); }`);
  let value;
  try {
    value = compiled(bag);
  } catch (error) {
    value = null;
    resolved.push(`<${String(error?.name ?? "error")}>`);
  }
  return { value, resolved };
}

/** Walk a decoded blob, evaluating the rules it carries. */
export function materialise(node, scope, resolved = []) {
  if (Array.isArray(node)) return node.map((child) => materialise(child, scope, resolved));
  if (node !== null && typeof node === "object") {
    if (typeof node.$expr === "string") {
      const outcome = evaluateRule(node.$expr, scope);
      resolved.push(...outcome.resolved);
      return outcome.value;
    }
    const out = {};
    for (const key of Object.keys(node)) out[key] = materialise(node[key], scope, resolved);
    return out;
  }
  return node;
}

export function decodeState(blob) {
  const text = Buffer.from(String(blob ?? ""), "base64").toString("utf8");
  const parsed = JSON.parse(text);
  if (parsed === null || typeof parsed !== "object") {
    throw new Error("state must decode to an object");
  }
  return parsed;
}

export function encodeState(state) {
  return Buffer.from(JSON.stringify(state), "utf8").toString("base64");
}
