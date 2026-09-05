/**
 * The error vocabulary every handler uses.
 *
 * One shape for every failure — `{ error: { code, message, details? } }` — because the
 * client has one error renderer, and because a handler that invents its own shape is a
 * handler the client will render as "something went wrong".
 */
export class ApiError extends Error {
  constructor(status, code, message, details) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export const badRequest = (message, details) => new ApiError(400, "bad_request", message, details);
export const unauthorized = (message = "Sign in to continue.") =>
  new ApiError(401, "unauthenticated", message);
export const forbidden = (message = "You do not have access to that.") =>
  new ApiError(403, "forbidden", message);
export const notFound = (message = "Not found.") => new ApiError(404, "not_found", message);
export const conflict = (message, details) => new ApiError(409, "conflict", message, details);
export const tooLarge = (message = "That file is too large.") =>
  new ApiError(413, "payload_too_large", message);
export const unprocessable = (message, details) =>
  new ApiError(422, "unprocessable", message, details);

/** Wrap an async handler so a rejected promise reaches the error middleware. */
export const wrap = (handler) => (req, res, next) => {
  Promise.resolve(handler(req, res, next)).catch(next);
};
