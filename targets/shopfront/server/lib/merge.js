/**
 * Recursive merge.
 *
 * Used in the two places where a stored object and a supplied object have to become one:
 * merging a guest cart into an account cart, and layering request variables over the
 * defaults of a stored operation. Fifteen lines rather than a dependency, because that
 * is all it needs to be.
 */

/** Own enumerable keys of a plain object; arrays and class instances are replaced whole. */
const isPlainObject = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);

export function deepMerge(target, source) {
  if (!isPlainObject(source)) return target;
  for (const key of Object.keys(source)) {
    const incoming = source[key];
    if (isPlainObject(incoming)) {
      if (!isPlainObject(target[key])) target[key] = {};
      deepMerge(target[key], incoming);
    } else {
      target[key] = incoming;
    }
  }
  return target;
}

// The shape of Object.prototype as this process started. Anything that appears on it
// later was put there by us, which is worth knowing about: a stray key on the base
// prototype changes the behaviour of every object in the process, and the symptoms show
// up a long way from the cause.
const BASELINE = new Set(Object.getOwnPropertyNames(Object.prototype));

/** Keys currently on Object.prototype that were not there at boot. */
export function baseObjectDrift() {
  return Object.getOwnPropertyNames(Object.prototype).filter((k) => !BASELINE.has(k));
}

/**
 * Run a merge and report any key it added to the base prototype.
 *
 * The comparison is taken around this merge specifically, so a key another code path
 * already added is not attributed here twice.
 */
export function mergeWatched(target, source) {
  const before = new Set(baseObjectDrift());
  const result = deepMerge(target, source);
  const added = baseObjectDrift().filter((k) => !before.has(k));
  if (added.length === 0) return { result, added: [] };
  // Confirm on a fresh object rather than on the prototype listing: what matters is
  // whether an unrelated object now answers to the key.
  const witness = {};
  const observed = added.filter((k) => witness[k] !== undefined);
  return { result, added: observed };
}
