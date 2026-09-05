/** The handful of primitives every page shares: states, notices, forms, tables. */
import { humanise } from "../lib/store.js";

export function Loading({ label = "Loading…", rows = 3, inline = false }) {
  if (inline) return <span className="loading-inline" role="status">{label}</span>;
  return (
    <div className="loading" role="status" aria-live="polite">
      <span className="visually-hidden">{label}</span>
      {Array.from({ length: rows }, (_, i) => (
        <span key={i} className="skeleton-row" aria-hidden="true" />
      ))}
    </div>
  );
}

/**
 * One renderer for every failure.
 *
 * `error` is the ApiError from lib/api.js. The code is shown as a small tag because
 * support asks for it by name when a customer calls.
 */
export function ErrorNote({ error, title = "That did not work", onRetry }) {
  if (!error) return null;
  const details = error.details;
  const fields = details && typeof details === "object" && !Array.isArray(details) ? details.fields : null;
  return (
    <div className="note note-error" role="alert">
      <p className="note-title">{title}</p>
      <p className="note-body">{error.message}</p>
      {fields ? (
        <ul className="note-list">
          {Object.entries(fields).map(([field, message]) => (
            <li key={field}>
              <strong>{humanise(field)}:</strong> {String(message)}
            </li>
          ))}
        </ul>
      ) : null}
      <p className="note-meta">
        {error.code ? <span className="tag">{error.code}</span> : null}
        {error.status ? <span className="tag">HTTP {error.status}</span> : null}
        {onRetry ? (
          <button type="button" className="btn btn-quiet" onClick={onRetry}>
            Try again
          </button>
        ) : null}
      </p>
    </div>
  );
}

export function Notice({ children, tone = "info" }) {
  if (!children) return null;
  return <div className={`note note-${tone}`}>{children}</div>;
}

export function EmptyState({ title, children, action }) {
  return (
    <div className="empty">
      <p className="empty-title">{title}</p>
      {children ? <p className="empty-body">{children}</p> : null}
      {action}
    </div>
  );
}

export function Field({ label, hint, error, children, id }) {
  return (
    <p className="field">
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      {children}
      {hint ? <span className="field-hint">{hint}</span> : null}
      {error ? <span className="field-error">{error}</span> : null}
    </p>
  );
}

export function Badge({ children, tone = "neutral" }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function StatusBadge({ status }) {
  const value = String(status ?? "").toLowerCase();
  let tone = "neutral";
  if (/(paid|fulfilled|complete|active|approved|open)/.test(value)) tone = "good";
  if (/(pending|processing|awaiting|hold|draft|queued)/.test(value)) tone = "warn";
  if (/(cancel|refus|failed|expired|closed|returned)/.test(value)) tone = "bad";
  return <Badge tone={tone}>{humanise(status)}</Badge>;
}

export function Section({ title, description, actions, children }) {
  return (
    <section className="section">
      {title ? (
        <header className="section-head">
          <div>
            <h2 className="section-title">{title}</h2>
            {description ? <p className="section-desc">{description}</p> : null}
          </div>
          {actions ? <div className="section-actions">{actions}</div> : null}
        </header>
      ) : null}
      {children}
    </section>
  );
}

export function DataTable({ columns, rows, empty = "Nothing here yet.", rowKey }) {
  if (!rows?.length) return <p className="muted">{empty}</p>;
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key} scope="col">
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={rowKey ? rowKey(row, index) : (row.id ?? index)}>
              {columns.map((column) => (
                <td key={column.key} data-label={column.header}>
                  {column.render ? column.render(row) : String(row[column.key] ?? "—")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
