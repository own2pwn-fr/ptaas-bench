/**
 * The two middlewares every API route ends at.
 *
 * Error rendering is centralised so that a client never has to guess the shape of a
 * failure, and so that the amount of internal detail on the wire is decided once.
 */
import { ApiError } from "./errors.js";

export function notFoundApi(req, res) {
  res.status(404).json({
    error: {
      code: "not_found",
      message: `Cannot ${req.method} ${req.path}`,
    },
  });
}

export function errorHandler(err, req, res, _next) {
  if (res.headersSent) return;

  if (err instanceof ApiError) {
    const body = { error: { code: err.code, message: err.message } };
    if (err.details) body.error.details = err.details;
    res.status(err.status).json(body);
    return;
  }

  // Malformed JSON reaches here from the body parser. It is a client mistake, not a
  // service failure, and it should not page anybody.
  if (err?.type === "entity.parse.failed") {
    res.status(400).json({ error: { code: "bad_request", message: "Malformed JSON body." } });
    return;
  }
  if (err?.type === "entity.too.large") {
    res.status(413).json({ error: { code: "payload_too_large", message: "That request is too large." } });
    return;
  }

  // Anything else is ours. The reference goes to the log with the stack; the client
  // gets the reference and nothing else, so support can find it without the browser
  // ever having seen an internal path.
  const reference = Math.random().toString(36).slice(2, 10);
  process.stderr.write(
    `[error] ref=${reference} ${req.method} ${req.originalUrl} ${err?.stack ?? err}\n`,
  );
  res.status(500).json({
    error: {
      code: "internal_error",
      message: "Something went wrong on our side. Please try again.",
      reference,
    },
  });
}
