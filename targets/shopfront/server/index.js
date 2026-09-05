/**
 * Storefront service entry point.
 *
 * Serves the React client and the JSON API behind it from one process. The client is a
 * single-page application: the server hands out one HTML shell for every page route and
 * the browser fetches everything else as JSON, so there is nothing in the initial
 * document except the shell.
 */
import { createServer } from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { initTelemetry, telemetryMiddleware } from "@internal/telemetry";
import cookieParser from "cookie-parser";
import express from "express";

import config from "./config.js";
import { attachSession } from "./lib/session.js";
import { installShell } from "./shell.js";
import { partnerCors, storefrontCors } from "./lib/cors.js";
import { errorHandler, notFoundApi } from "./lib/handlers.js";
import { attachStreams } from "./routes/streams.js";
import accountRouter from "./routes/account.js";
import adminRouter from "./routes/admin.js";
import authRouter from "./routes/auth.js";
import cartRouter from "./routes/cart.js";
import catalogRouter from "./routes/catalog.js";
import checkoutRouter from "./routes/checkout.js";
import contentRouter from "./routes/content.js";
import giftCardRouter from "./routes/giftcards.js";
import graphqlRouter from "./routes/graphql.js";
import metaRouter from "./routes/meta.js";
import ordersRouter from "./routes/orders.js";
import supportRouter from "./routes/support.js";

// Reads TELEMETRY_SERVICE and TELEMETRY_ENDPOINT. With neither set the client is inert
// and the service behaves exactly as it does when the collector is down.
initTelemetry();

const here = path.dirname(fileURLToPath(import.meta.url));
export const webRoot = path.resolve(here, "..", "web", "dist");

export const app = express();

// Express advertises itself in X-Powered-By by default. The platform team turned it off
// across every service years ago; the reverse proxy sets its own Server header.
app.disable("x-powered-by");
app.set("trust proxy", true);
app.set("etag", "strong");

// First, ahead of the body parsers: the route accessor has to be installed before
// routing runs. Request attributes are still collected from the parsed body, because
// that work happens once the response has been flushed.
app.use(telemetryMiddleware({ ignore: (req) => req.url === "/api/status" }));

app.use(cookieParser());
// Policy reports arrive with their own media types; the browser will not negotiate.
app.use(
  express.json({
    limit: "512kb",
    type: ["application/json", "application/csp-report", "application/reports+json"],
  }),
);
app.use(express.urlencoded({ extended: false, limit: "128kb" }));
app.use(attachSession);

// Customer uploads. Served from the volume rather than from the bundle, because they
// outlive a deploy and because the CDN is meant to sit in front of this path.
app.use(
  "/media",
  express.static(config.mediaDir, { index: false, redirect: false, maxAge: "7d", fallthrough: true }),
);

// The document shell, the report-only content security policy and the static assets.
installShell(app, webRoot);

// The storefront's own origins. Everything under /api except the account surface is
// answered with the strict list.
app.use("/api", storefrontCors);

app.use("/api", metaRouter);
app.use("/api", catalogRouter);
app.use("/api", contentRouter);
app.use("/api/auth", authRouter);
// The partner widget embeds the account summary cross-origin, so this surface has its
// own, wider policy. See lib/cors.js.
app.use("/api/account", partnerCors, accountRouter);
app.use("/api/cart", cartRouter);
app.use("/api/checkout", checkoutRouter);
app.use("/api/orders", ordersRouter);
app.use("/api/support", supportRouter);
app.use("/api", giftCardRouter);
app.use("/api/admin", adminRouter);
app.use("/graphql", graphqlRouter);

// Unmatched API paths answer JSON; unmatched page paths get the client shell, which is
// what makes deep links work in a single-page application.
app.use("/api", notFoundApi);
app.use(errorHandler);

const server = createServer(app);
attachStreams(server);

const invokedDirectly = process.argv[1] && import.meta.url.endsWith(path.basename(process.argv[1]));
if (invokedDirectly) {
  server.listen(config.port, () => {
    process.stdout.write(`storefront listening on :${config.port}\n`);
  });
}

export { server };
