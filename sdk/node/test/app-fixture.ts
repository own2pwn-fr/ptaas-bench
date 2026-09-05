import express, { type Express } from "express";
import multer from "multer";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";

import { TelemetryClient } from "../src/client.js";
import { telemetryMiddleware } from "../src/middleware.js";

const upload = multer({ storage: multer.memoryStorage() });

export interface TestApp {
  url: string;
  app: Express;
  close(): Promise<void>;
}

/**
 * Express 5 app covering every route shape the middleware has to handle: a flat route,
 * a mounted router, a router nested inside a router, a route with several params, an
 * array route, and a catch-all 404 (which must report `<unmatched>`).
 */
export function buildApp(client: TelemetryClient): Express {
  const app = express();
  app.use(telemetryMiddleware({ client }));
  app.use(express.json());
  app.use(express.urlencoded({ extended: true }));
  app.use(express.text({ type: "text/plain" }));
  app.use(express.raw({ type: "application/octet-stream" }));

  app.get("/health", (_req, res) => {
    res.json({ ok: true });
  });

  const orders = express.Router();
  orders.get("/:id", (req, res) => {
    res.json({ id: req.params.id });
  });
  orders.post("/:id/items/:sku", (req, res) => {
    res.json({ id: req.params.id, sku: req.params.sku });
  });

  const api = express.Router();
  api.get("/products", (req, res) => {
    res.json({ q: req.query.q ?? null });
  });
  api.post("/admin/imports", (_req, res) => {
    res.status(201).json({ queued: true });
  });
  api.post("/upload", upload.any(), (_req, res) => {
    res.json({ ok: true });
  });
  api.get(["/alias-a", "/alias-b"], (_req, res) => {
    res.json({ ok: true });
  });
  // Nested mount: the reported template must be /api/orders/:id, i.e. both prefixes
  // glued back onto the router-local path.
  api.use("/orders", orders);

  app.use("/api", api);

  app.get("/", (_req, res) => {
    res.json({ root: true });
  });

  app.use((_req, res) => {
    res.status(404).json({ error: "not found" });
  });

  return app;
}

export async function listen(app: Express): Promise<TestApp> {
  const server: Server = createServer(app);
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address() as AddressInfo;
  return {
    app,
    url: `http://127.0.0.1:${port}`,
    close: () => new Promise<void>((resolve) => server.close(() => resolve())),
  };
}
