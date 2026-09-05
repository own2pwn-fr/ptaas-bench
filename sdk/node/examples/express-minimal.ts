/**
 * Minimal service wiring: telemetry middleware, one router, one anomaly counter.
 *
 *   node --experimental-strip-types examples/express-minimal.ts
 *
 * Environment: TELEMETRY_SERVICE, TELEMETRY_ENDPOINT (see the platform runbook).
 */
import express from "express";
import { pathToFileURL } from "node:url";

import { initTelemetry, telemetry, telemetryMiddleware } from "../src/index.js";

// Reads TELEMETRY_SERVICE and TELEMETRY_ENDPOINT. With neither set the client is inert
// and the service behaves exactly as it does in production with the collector down.
initTelemetry();

const app = express();

// First, ahead of the body parsers: the route accessor has to be installed before
// routing runs. Request attributes are still collected from the parsed body, because
// that happens once the response has been flushed.
app.use(telemetryMiddleware());
app.use(express.json());

// ---------------------------------------------------------------------------
// Storage layer. Stands in for the reporting database: what matters here is that the
// statement text and the returned rows are both inspectable, because the anomaly
// counter below is defined over the two together.
// ---------------------------------------------------------------------------
interface Row {
  __table: string;
  [column: string]: unknown;
}

const TABLES: Record<string, Row[]> = {
  products: [
    { __table: "products", id: 1, name: 'Laptop 14"', price: 999 },
    { __table: "products", id: 2, name: "Laptop stand", price: 39 },
  ],
  users: [{ __table: "users", id: 1, email: "ada@example.com", password_hash: "$2b$12$…" }],
};

function executeSql(sql: string): Row[] {
  const union = /union\s+select\b[\s\S]*?\bfrom\s+([a-z_]+)/i.exec(sql);
  if (union) {
    const table = union[1] ?? "";
    return [...(TABLES.products ?? []), ...(TABLES[table] ?? [])];
  }
  if (/\bor\s+'?1'?\s*=\s*'?1/i.test(sql)) return TABLES.products ?? [];
  const like = /name\s+like\s+'%([^%]*)%'/i.exec(sql);
  const needle = (like?.[1] ?? "").toLowerCase();
  return (TABLES.products ?? []).filter((r) => String(r.name).toLowerCase().includes(needle));
}

const api = express.Router();

api.get("/products", (req, res) => {
  const q = String(req.query.q ?? "");

  const sql = `SELECT id, name, price FROM products WHERE name LIKE '%${q}%'`;
  const rows = executeSql(sql);

  // Query-plan anomaly counter.
  //
  // It increments on the executed plan, never on the input: a search term that merely
  // looks unusual is not an anomaly, and counting those would drown the metric in
  // noise from ordinary customer searches containing quotes or the word "union". What
  // is counted here is a plan that reached outside the products table, or returned more
  // rows than that table holds — an effect, observed after the fact.
  const widenedPlan = /\bunion\s+select\b/i.test(sql) || /'\s*or\s+'?1'?\s*=\s*'?1/i.test(sql);
  const foreignRows = rows.some((row) => row.__table !== "products");
  if (widenedPlan && (foreignRows || rows.length > (TABLES.products?.length ?? 0))) {
    telemetry.signal("shop.catalog.query.plan_anomaly", {
      payload: q,
      detail: `plan widened; ${rows.length} row(s) returned from ${[
        ...new Set(rows.map((r) => r.__table)),
      ].join(",")}`,
    });
  }

  res.json({ products: rows.map(({ __table, ...row }) => row) });
});

/**
 * Supplier catalogue import.
 *
 * The destination comes from the caller, so it is declared before the fetch: the
 * network's resolver logs every lookup, and without this the log line cannot be
 * attributed back to the request that caused it.
 */
api.post("/imports", async (req, res) => {
  const sourceUrl = String((req.body as { source_url?: unknown })?.source_url ?? "");
  let host = "";
  try {
    host = new URL(sourceUrl).hostname;
  } catch {
    res.status(400).json({ error: "source_url must be an absolute URL" });
    return;
  }

  const requestId = telemetry.correlate({
    signal: "shop.imports.fetch.egress",
    destinationHost: host,
    route: "/api/imports",
    param: "source_url",
  });

  try {
    await fetch(sourceUrl, { signal: AbortSignal.timeout(5_000) });
  } catch {
    // The importer discards the response either way; failures are retried by the job.
  }
  res.status(202).json({ queued: true, request_id: requestId });
});

// Mounted under a prefix: the recorded template is `/api/products`, not the
// router-local `/products` and not the concrete URL.
app.use("/api", api);

app.get("/", (_req, res) => {
  res.json({ service: "catalog" });
});

app.use((_req, res) => {
  res.status(404).json({ error: "not found" });
});

export { app };

// Standard ESM entrypoint guard, so the module can also be imported by a smoke test or
// an in-process harness without binding a port.
const invokedDirectly =
  process.argv[1] !== undefined && import.meta.url === pathToFileURL(process.argv[1]).href;
if (invokedDirectly) {
  app.listen(Number(process.env.PORT ?? 3000));
}
