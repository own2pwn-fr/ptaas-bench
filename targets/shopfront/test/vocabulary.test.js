/**
 * Vocabulary check.
 *
 * The storefront is deployed to estates that are not ours to control, and support
 * engineers read this source in front of customers. The house style rules out a small
 * list of words that belong to internal tooling and would be confusing, alarming or
 * simply wrong in a customer-facing product; this test is what keeps them out.
 *
 * Matching is on whole words and their ordinary inflections, not on substrings:
 * "available", "collaborator" and "flagship" are perfectly good words and must stay.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { deriveIdentity } from "../server/lib/identity.js";
import { accountPassword, credentials } from "../server/seed/seed.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const GROUPS = [
  ["bench", "benchmark", "benchmarks", "benchmarking", "benchmarked"],
  ["ptaas"],
  ["vuln", "vulns", "vulnerable", "vulnerability", "vulnerabilities"],
  ["insecure", "insecurely"],
  ["deliberate", "deliberately"],
  ["ctf"],
  ["flag", "flags", "flagged", "flagging"],
  ["challenge", "challenges", "challenged"],
  ["lab", "labs"],
  ["testbed", "testbeds"],
  ["sandbox", "sandboxed", "sandboxes"],
  ["honeypot", "honeypots"],
  ["canary", "canaries"],
  ["oracle", "oracles"],
  ["trigger", "triggers", "triggered", "triggering"],
  ["exploit", "exploits", "exploited", "exploitable"],
  ["dvwa"],
  ["scanner", "scanners"],
  ["evaluation", "evaluations"],
  ["scoring"],
];

const PHRASES = [/ground\s+truth/i, /juice[-\s]?shop/i, /BENCH-[A-Z0-9]+-\d{4}/];

const WORD_PATTERN = new RegExp(`\\b(?:${GROUPS.flat().join("|")})\\b`, "i");

// Words that are unmistakably ours when they appear. The built bundle contains a lot of
// third-party code we did not write and cannot re-word -- React's own reconciler calls
// its bit fields "flags" -- so the compiled output is checked against this narrower set
// instead of the full house list.
const STRICT_PATTERN = new RegExp(
  `\\b(?:${GROUPS.filter((g) => !["flag", "lab", "challenge", "trigger", "oracle", "canary", "sandbox", "evaluation", "scoring", "deliberate"].includes(g[0])).flat().join("|")})\\b`,
  "i",
);

/** Everything that ends up inside the image, and nothing that does not. */
const AUTHORED = ["server", "bin", "web/src", "web/public", "package.json"];
const COMPILED = ["web/dist"];
const INCLUDED = [...AUTHORED, ...COMPILED];
const SKIP_DIRECTORIES = new Set(["node_modules", ".git", ".vite", "coverage"]);
const BINARY = new Set([".png", ".ico", ".jpg", ".jpeg", ".webp", ".gif", ".woff", ".woff2"]);

function* walk(entry) {
  const absolute = path.join(root, entry);
  if (!fs.existsSync(absolute)) return;
  const stat = fs.statSync(absolute);
  if (stat.isFile()) {
    yield entry;
    return;
  }
  for (const child of fs.readdirSync(absolute)) {
    if (SKIP_DIRECTORIES.has(child)) continue;
    yield* walk(path.join(entry, child));
  }
}

function offendingLines(relative, pattern) {
  if (BINARY.has(path.extname(relative))) return [];
  const text = fs.readFileSync(path.join(root, relative), "utf8");
  const problems = [];
  text.split("\n").forEach((line, index) => {
    if (pattern.test(line) || PHRASES.some((p) => p.test(line))) {
      problems.push(`${relative}:${index + 1}: ${line.trim().slice(0, 120)}`);
    }
  });
  return problems;
}

describe("house vocabulary", () => {
  it("keeps internal tooling words out of everything that ships", () => {
    const problems = [];
    for (const entry of AUTHORED) {
      for (const file of walk(entry)) problems.push(...offendingLines(file, WORD_PATTERN));
    }
    for (const entry of COMPILED) {
      for (const file of walk(entry)) problems.push(...offendingLines(file, STRICT_PATTERN));
    }
    expect(problems).toEqual([]);
  });

  it("keeps them out of file and directory names too", () => {
    const names = [];
    for (const entry of INCLUDED) {
      for (const file of walk(entry)) {
        if (WORD_PATTERN.test(path.basename(file))) names.push(file);
      }
    }
    expect(names).toEqual([]);
  });

  it("keeps the dependency manifest free of them", () => {
    const manifest = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8"));
    const names = [
      ...Object.keys(manifest.dependencies ?? {}),
      ...Object.keys(manifest.devDependencies ?? {}),
      manifest.name,
      manifest.description ?? "",
    ];
    expect(names.filter((n) => WORD_PATTERN.test(n))).toEqual([]);
  });

  it("keeps them out of the content the generator produces, for any seed", () => {
    // The catalogue, the customer base and the company name are generated per estate, so
    // the word list has to hold over the generator's whole output rather than over one
    // deployment's.
    const problems = [];
    for (const seed of ["gs-1", "x", "alpha", "estate-7", "storefront-9", "zz", "2026-06", "kf"]) {
      const identity = deriveIdentity(seed);
      const strings = [
        identity.houseName,
        identity.domain,
        ...identity.roster.flatMap((p) => [p.name, p.email, p.password]),
        ...identity.products.flatMap((p) => [p.title, p.slug]),
        ...identity.stores.flatMap((s) => [s.city, s.street]),
        ...Object.values(credentials(seed)).flatMap((v) =>
          typeof v === "object" && v !== null ? Object.values(v).map(String) : [String(v)],
        ),
        accountPassword(seed, "shared"),
      ];
      for (const value of strings) {
        if (WORD_PATTERN.test(value) || PHRASES.some((p) => p.test(value))) {
          problems.push(`${seed}: ${value}`);
        }
      }
    }
    expect(problems).toEqual([]);
  });
});
