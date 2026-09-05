/**
 * Reference target application: the shape every ptaas-bench target copies.
 *
 * It plants BENCH-SHOP-0001 (UNION-based SQLi in the product search filter) exactly as
 * the catalog describes it, and shows where `bench.trigger` belongs relative to the
 * vulnerable sink.
 *
 *   node --experimental-strip-types examples/express-minimal.ts
 *   BENCH_APP=shopfront BENCH_COLLECTOR_URL=http://collector:8900 node ... (under compose)
 */
import express from "express";
import { pathToFileURL } from "node:url";

import { benchMiddleware, bench, initBench } from "../src/index.js";

// Reads BENCH_APP and BENCH_COLLECTOR_URL. Absent either, the SDK is inert and this
// app behaves exactly as it does in production — that is the point: a target is not
// allowed to look different when it is not being benchmarked.
initBench();

const app = express();

// First, before the body parsers: the route snapshot has to be installed before
// routing runs. Parameter enumeration still sees the parsed body, because it happens
// after the response has been flushed.
app.use(benchMiddleware());
app.use(express.json());

// ---------------------------------------------------------------------------
// Fake data layer. A real target would use a real database; what matters for the
// benchmark is that the SQL text and the returned rows are both inspectable, since
// the oracle is defined over both.
// ---------------------------------------------------------------------------
interface Row {
  __table: string;
  [column: string]: unknown;
}

const TABLES: Record<string, Row[]> = {
  products: [
    { __table: "products", id: 1, name: "Laptop 14\"", price: 999 },
    { __table: "products", id: 2, name: "Laptop stand", price: 39 },
  ],
  users: [{ __table: "users", id: 1, email: "ada@example.com", password_hash: "$2b$12$…" }],
};

/**
 * Toy engine standing in for a driver: it "parses" the statement and, when the query
 * carries a UNION against another table, actually returns that table's rows. The flaw
 * has to be real — a scanner must be able to exfiltrate data, not just see an error.
 */
function executeSql(sql: string): Row[] {
  const union = /union\s+select\b[\s\S]*?\bfrom\s+([a-z_]+)/i.exec(sql);
  if (union) {
    const table = union[1] ?? "";
    return [...(TABLES.products ?? []), ...(TABLES[table] ?? [])];
  }
  const like = /name\s+like\s+'%([^%]*)%'/i.exec(sql);
  const needle = (like?.[1] ?? "").toLowerCase();
  if (/\bor\s+'?1'?\s*=\s*'?1/i.test(sql)) return TABLES.products ?? [];
  return (TABLES.products ?? []).filter((r) => String(r.name).toLowerCase().includes(needle));
}

// ---------------------------------------------------------------------------
// BENCH-SHOP-0001 — UNION-based SQL injection in the product search filter.
// entrypoint: GET /api/products?q=  (default_value: "laptop")
// oracle.kind: sink
// ---------------------------------------------------------------------------
const api = express.Router();

api.get("/products", (req, res) => {
  const q = String(req.query.q ?? "");

  // THE PLANTED FLAW: string concatenation, no parameter binding.
  const sql = `SELECT id, name, price FROM products WHERE name LIKE '%${q}%'`;
  const rows = executeSql(sql);

  // THE ORACLE. Note what is being checked: not "did a payload arrive" (that is
  // merely `exercise`, and the scorer derives it from the http_request event on its
  // own), but "did the injected clause change what the database returned". A scanner
  // that gets a quote reflected back scores exercise; only real exfiltration or a
  // real tautology reaches this branch.
  const injectedClause = /\bunion\s+select\b/i.test(sql) || /'\s*or\s+'?1'?\s*=\s*'?1/i.test(sql);
  const foreignRows = rows.some((row) => row.__table !== "products");
  if (injectedClause && (foreignRows || rows.length > (TABLES.products?.length ?? 0))) {
    bench.trigger("BENCH-SHOP-0001", {
      oracleKind: "sink",
      payload: q,
      detail: `injected clause parsed by the engine; ${rows.length} row(s) returned, tables: ${[
        ...new Set(rows.map((r) => r.__table)),
      ].join(",")}`,
    });
  }

  res.json({ products: rows.map(({ __table, ...row }) => row) });
});

// Mounted under a prefix on purpose: the middleware must report the composed template
// `/api/products`, not the router-local `/products` and not the concrete URL.
app.use("/api", api);

// An unmatched request still produces an event, with route "<unmatched>" and the real
// path in `path`, so the scorer can see what a crawler probed for and missed.
app.use((_req, res) => {
  res.status(404).json({ error: "not found" });
});

export { app };

// Standard ESM entrypoint guard, so the app can also be imported (by a smoke test, or
// by a harness that wants to drive it in-process) without binding a port.
const invokedDirectly =
  process.argv[1] !== undefined && import.meta.url === pathToFileURL(process.argv[1]).href;
if (invokedDirectly) {
  app.listen(Number(process.env.PORT ?? 3000));
}
