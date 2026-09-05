import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { beforeAll, describe, expect, it } from "vitest";

/**
 * The SDK ships inside the target containers, so anything a reader of that filesystem
 * could find has to look like an ordinary internal observability package.
 *
 * The forbidden list is copied from `targets/target-contract.yaml`, section
 * `deception`. It is duplicated here rather than parsed from that file because the
 * check must keep working when this package is built on its own, and because a stale
 * copy fails loudly (a term removed upstream just makes this stricter than needed)
 * whereas a missing file would fail open.
 *
 * This is the kind of property that rots back in silently: one debugging comment, one
 * renamed symbol, and the cover is gone. So it is a test, not a review item.
 */
const FORBIDDEN_TERMS = [
  // Exactly the contract's list.
  "bench",
  "benchmark",
  "ptaas-bench",
  "vuln",
  "vulnerable",
  "insecure",
  "deliberately",
  "ctf",
  "flag",
  "challenge",
  "lab",
  "testbed",
  "sandbox",
  "honeypot",
  "canary",
  "oracle",
  "ground truth",
  "trigger",
  "exploit-me",
  "dvwa",
  "juice-shop",
  "scanner",
  "evaluation",
  "scoring",
  // Not in the contract's list, but just as revealing in a comment.
  "grader",
  "answer key",
  "pentest",
  "attacker",
  "adversary",
  "corpus",
  "planted",
  "instrumented target",
  "under test",
];

/**
 * Terms whose inflections are equally revealing.
 *
 * Checked as prefixes, unlike the rest. "vulnerability" and "benchmarking" are not the
 * literal entries above, but nobody would accept them either.
 */
const FORBIDDEN_STEMS = ["bench", "vuln", "exploit", "scan", "honeypot", "ctf", "dvwa", "ptaas"];

/**
 * Match on words, not on substrings.
 *
 * A naive `includes()` fails on ordinary English: "available" and "collaborator" both
 * contain "lab", "elaborate" contains "lab" too. Text is therefore split into words —
 * on punctuation *and* on camelCase boundaries, so `benchMiddleware` yields
 * ["bench", "middleware"] — and matched as whole words or as a multi-word phrase.
 */
export function tokenize(text: string): string[] {
  return text
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .split(/[^A-Za-z0-9]+/)
    .filter(Boolean)
    .map((w) => w.toLowerCase());
}

export function findForbidden(text: string): string[] {
  const tokens = tokenize(text);
  const stream = ` ${tokens.join(" ")} `;
  const hits = new Set<string>();

  for (const term of FORBIDDEN_TERMS) {
    const phrase = tokenize(term).join(" ");
    if (phrase && stream.includes(` ${phrase} `)) hits.add(term);
  }
  for (const stem of FORBIDDEN_STEMS) {
    if (tokens.some((token) => token.startsWith(stem))) hits.add(`${stem}*`);
  }
  return [...hits].sort();
}

const ROOT = resolve(import.meta.dirname, "..");

/**
 * Everything that can end up inside a container: the published bundle, the manifest,
 * and the sources — the bundle carries a source map whose `sourcesContent` embeds every
 * comment, and a service may vendor `src/` or copy `examples/` outright.
 *
 * `test/` is excluded on purpose. It never ships, and it is the one place where the
 * platform's own vocabulary is allowed to live.
 */
const SCANNED_ROOTS = ["dist", "src", "examples", "package.json"];

function walk(path: string, out: string[]): void {
  if (!existsSync(path)) return;
  const stats = statSync(path);
  if (stats.isFile()) {
    out.push(path);
    return;
  }
  for (const entry of readdirSync(path)) walk(join(path, entry), out);
}

function shippedFiles(): string[] {
  const out: string[] = [];
  for (const root of SCANNED_ROOTS) walk(join(ROOT, root), out);
  return out;
}

describe("word matcher", () => {
  it("matches whole words and camelCase segments", () => {
    expect(findForbidden("const benchMiddleware = 1")).toContain("bench");
    expect(findForbidden("BENCH_APP=shopfront")).toContain("bench");
    expect(findForbidden("this is a ptaas-bench sdk")).toContain("ptaas-bench");
    expect(findForbidden("the ground truth for this run")).toContain("ground truth");
    expect(findForbidden("vulnerabilities were found")).toContain("vuln*");
  });

  it("does not fire on ordinary English that merely contains a term", () => {
    // The reason this check tokenises instead of using includes().
    expect(findForbidden("no collaborator is available; elaborate labelling")).toEqual([]);
    expect(findForbidden("the interface is stable and configurable")).toEqual([]);
  });

  it("errs towards over-strictness on stems", () => {
    // "scandinavian" starts with "scan", so it trips the stem check. That is the
    // intended trade-off: a false positive costs one rewritten comment, a false
    // negative costs the cover of every target in the fleet.
    expect(findForbidden("scandinavian")).toEqual(["scan*"]);
  });
});

describe("shipped artefacts carry no revealing vocabulary", () => {
  beforeAll(() => {
    // The bundle and its source map are part of what ships, so the check runs against
    // a fresh build rather than whatever happens to be on disk.
    execFileSync("npx", ["tsup"], { cwd: ROOT, stdio: "pipe" });
  });

  it("scans a non-trivial number of files, so a broken glob cannot pass silently", () => {
    const files = shippedFiles();
    expect(files.length).toBeGreaterThan(10);
    expect(files.some((f) => f.endsWith("dist/index.js"))).toBe(true);
    expect(files.some((f) => f.endsWith("dist/index.js.map"))).toBe(true);
    expect(files.some((f) => f.endsWith("package.json"))).toBe(true);
  });

  it("finds no forbidden term in any shipped file, contents or name", () => {
    const offenders: string[] = [];
    for (const file of shippedFiles()) {
      const rel = relative(ROOT, file);
      const inName = findForbidden(rel);
      if (inName.length > 0) offenders.push(`${rel} [filename]: ${inName.join(", ")}`);
      const inBody = findForbidden(readFileSync(file, "utf8"));
      if (inBody.length > 0) offenders.push(`${rel}: ${inBody.join(", ")}`);
    }
    expect(offenders).toEqual([]);
  });

  it("declares a package identity that reads like an internal library", () => {
    const pkg = JSON.parse(readFileSync(join(ROOT, "package.json"), "utf8")) as {
      name: string;
      description: string;
      private?: boolean;
    };
    expect(pkg.name).toBe("@internal/telemetry");
    expect(findForbidden(`${pkg.name} ${pkg.description}`)).toEqual([]);
    // Never publishable to a public registry by accident.
    expect(pkg.private).toBe(true);
  });

  it("names no environment variable that would give the platform away", () => {
    const source = shippedFiles()
      .filter((f) => f.endsWith(".ts") || f.endsWith(".js") || f.endsWith(".cjs"))
      .map((f) => readFileSync(f, "utf8"))
      .join("\n");
    // Only names actually read off the environment, not every SHOUTY constant.
    const envNames = [...source.matchAll(/\benv\.([A-Z][A-Z0-9_]*)\b/g)].map((m) => m[1]!);

    expect(new Set(envNames)).toEqual(
      new Set([
        "TELEMETRY_SERVICE",
        "TELEMETRY_ENDPOINT",
        "TELEMETRY_ENABLED",
        "TELEMETRY_EVENTS_PATH",
        "TELEMETRY_CORRELATIONS_PATH",
        "TELEMETRY_SYNTHETIC_CIDRS",
        // The example service reads this one; every other name is the client's.
        "PORT",
      ]),
    );
    for (const name of new Set(envNames)) expect(findForbidden(name)).toEqual([]);
  });

  it("emits no identifier a reader could match against the platform", () => {
    const bundle = readFileSync(join(ROOT, "dist", "index.js"), "utf8");
    // Catalog identifiers, the old self-test header and the old event type are the
    // three strings that used to make this package identifiable at a glance.
    expect(bundle).not.toMatch(/BENCH-[A-Z0-9]+-\d{4}/);
    expect(bundle.toLowerCase()).not.toContain("selftest");
    expect(bundle.toLowerCase()).not.toContain("self-test");
  });
});
