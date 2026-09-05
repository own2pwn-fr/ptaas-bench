/**
 * The single entry point to the JSON API.
 *
 * Every call in the client goes through here so that three decisions are made once:
 * cookies travel with the request, bodies are JSON in and JSON out, and a failure is
 * always an ApiError carrying the server's `{ error: { code, message, details } }`.
 */
import { useCallback, useEffect, useRef, useState } from "react";

export class ApiError extends Error {
  constructor(status, code, message, details) {
    super(message || "Request failed");
    this.name = "ApiError";
    this.status = status;
    this.code = code || "unknown";
    this.details = details;
  }

  /** Field-level messages, when the server sent them, keyed by field name. */
  get fieldErrors() {
    const d = this.details;
    if (!d || typeof d !== "object" || Array.isArray(d)) return {};
    return d.fields && typeof d.fields === "object" ? d.fields : d;
  }
}

function buildUrl(path, query) {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    params.set(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${path}${path.includes("?") ? "&" : "?"}${qs}` : path;
}

export async function request(path, options = {}) {
  const { method = "GET", body, query, headers, signal } = options;
  let response;
  try {
    response = await fetch(buildUrl(path, query), {
      method,
      credentials: "same-origin",
      signal,
      headers: {
        accept: "application/json",
        ...(body === undefined ? {} : { "content-type": "application/json" }),
        ...headers,
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (cause) {
    if (cause?.name === "AbortError") throw cause;
    throw new ApiError(0, "network_error", "We could not reach the shop. Check your connection.");
  }

  if (response.status === 204) return null;

  const text = await response.text();
  let parsed = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = null;
    }
  }

  if (!response.ok) {
    const err = parsed?.error ?? {};
    throw new ApiError(
      response.status,
      err.code,
      err.message || `The shop answered ${response.status}.`,
      err.details,
    );
  }
  return parsed;
}

export const api = {
  get: (path, query, options) => request(path, { ...options, method: "GET", query }),
  post: (path, body, options) => request(path, { ...options, method: "POST", body }),
  put: (path, body, options) => request(path, { ...options, method: "PUT", body }),
  patch: (path, body, options) => request(path, { ...options, method: "PATCH", body }),
  del: (path, options) => request(path, { ...options, method: "DELETE" }),
};

/** POST an operation to the GraphQL endpoint and unwrap `data`. */
export async function graphql(query, variables, operationName) {
  const result = await api.post("/graphql", { query, variables, operationName });
  if (result?.errors?.length) {
    const first = result.errors[0];
    throw new ApiError(200, "graphql_error", first.message || "The catalogue query failed.", result.errors);
  }
  return result?.data ?? null;
}

/**
 * Read a resource for a component.
 *
 * Returns `{ data, error, loading, reload, setData }`. `deps` behaves like the
 * dependency array of an effect: change it and the loader runs again. In-flight work is
 * abandoned when the component unmounts or the deps change, so a slow page cannot
 * overwrite a fast one.
 */
export function useResource(loader, deps = [], options = {}) {
  const { skip = false, initial = null } = options;
  const [state, setState] = useState({ data: initial, error: null, loading: !skip });
  const [nonce, setNonce] = useState(0);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  useEffect(() => {
    if (skip) {
      setState({ data: initial, error: null, loading: false });
      return undefined;
    }
    let live = true;
    const controller = new AbortController();
    setState((prev) => ({ ...prev, loading: true, error: null }));
    Promise.resolve(loaderRef.current({ signal: controller.signal }))
      .then((data) => {
        if (live) setState({ data, error: null, loading: false });
      })
      .catch((error) => {
        if (!live || error?.name === "AbortError") return;
        setState({ data: null, error, loading: false });
      });
    return () => {
      live = false;
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce, skip]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  const setData = useCallback((next) => {
    setState((prev) => ({ ...prev, data: typeof next === "function" ? next(prev.data) : next }));
  }, []);

  return { ...state, reload, setData };
}

/** Small helper for form submits: tracks pending state and the resulting error. */
export function useAction() {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(null);

  const run = useCallback(async (fn) => {
    setPending(true);
    setError(null);
    try {
      return await fn();
    } catch (caught) {
      setError(caught);
      return undefined;
    } finally {
      setPending(false);
    }
  }, []);

  return { pending, error, setError, run };
}
