import { useState } from "react";

import { DataTable, ErrorNote, Loading, Notice, Section, StatusBadge } from "../../components/ui.jsx";
import { api, useAction, useResource } from "../../lib/api.js";
import { formatDateTime, listOf } from "../../lib/store.js";

/**
 * Catalogue imports.
 *
 * Suppliers publish a feed and we pull it: the address goes in here, the job fetches it
 * and the run shows up in the table with the counts it produced.
 */
export default function AdminImports() {
  const imports = useResource(({ signal }) => api.get("/api/admin/imports", null, { signal }), []);
  const [sourceUrl, setSourceUrl] = useState("");
  const action = useAction();
  const [notice, setNotice] = useState(null);

  const submit = async (event) => {
    event.preventDefault();
    const done = await action.run(() => api.post("/api/admin/imports", { source_url: sourceUrl.trim() }));
    if (done !== undefined) {
      setNotice("Import queued. It usually takes a couple of minutes.");
      setSourceUrl("");
      imports.reload();
    }
  };

  return (
    <>
      <Section title="Catalogue imports" actions={<button type="button" className="btn btn-quiet" onClick={imports.reload}>Refresh</button>}>
        {imports.loading ? <Loading rows={2} /> : null}
        <ErrorNote error={imports.error} title="The import history did not load" onRetry={imports.reload} />
        <DataTable
          empty="No imports run yet."
          rows={listOf(imports.data, "imports")}
          columns={[
            { key: "started_at", header: "Started", render: (row) => formatDateTime(row.started_at ?? row.created_at) },
            { key: "source_url", header: "Feed", render: (row) => <span className="mono">{row.source_url ?? "—"}</span> },
            { key: "status", header: "State", render: (row) => <StatusBadge status={row.status} /> },
            { key: "created_count", header: "Added", render: (row) => String(row.created_count ?? 0) },
            { key: "updated_count", header: "Updated", render: (row) => String(row.updated_count ?? 0) },
            { key: "message", header: "Note", render: (row) => row.message ?? row.error ?? "—" },
          ]}
        />
      </Section>

      <Section title="Run one now">
        <form className="card stack narrow-form" onSubmit={submit}>
          {notice ? <Notice tone="good">{notice}</Notice> : null}
          <label className="field-label" htmlFor="import-url">Feed address</label>
          <input
            id="import-url"
            className="input"
            type="url"
            placeholder="https://supplier.example/feed.csv"
            value={sourceUrl}
            onChange={(event) => setSourceUrl(event.target.value)}
            required
          />
          <span className="field-hint">CSV or JSON. The columns are matched by name.</span>
          <ErrorNote error={action.error} title="The import was not started" />
          <button type="submit" className="btn btn-primary" disabled={action.pending}>
            {action.pending ? "Queuing…" : "Start import"}
          </button>
        </form>
      </Section>
    </>
  );
}
