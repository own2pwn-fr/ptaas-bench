import { useState } from "react";

import { DataTable, ErrorNote, Loading, Section, StatusBadge } from "../../components/ui.jsx";
import { api, useAction, useResource } from "../../lib/api.js";
import { formatDateTime, listOf } from "../../lib/store.js";

export default function AdminSupport() {
  const [status, setStatus] = useState("open");
  const [opened, setOpened] = useState(null);
  const [reply, setReply] = useState("");
  const send = useAction();

  const tickets = useResource(
    ({ signal }) => api.get("/api/admin/support/tickets", { status, limit: 50 }, { signal }),
    [status],
  );
  const thread = useResource(
    ({ signal }) => api.get(`/api/support/tickets/${encodeURIComponent(opened)}/messages`, null, { signal }),
    [opened],
    { skip: !opened },
  );

  const submit = async (event) => {
    event.preventDefault();
    const done = await send.run(() =>
      api.post(`/api/support/tickets/${encodeURIComponent(opened)}/messages`, { body: reply }),
    );
    if (done !== undefined) {
      setReply("");
      thread.reload();
      tickets.reload();
    }
  };

  return (
    <>
      <Section
        title="Customer service"
        actions={
          <label className="inline-field">
            <span className="muted small">Show</span>
            <select className="input" value={status} onChange={(event) => setStatus(event.target.value)}>
              <option value="open">Open</option>
              <option value="pending">Waiting on the customer</option>
              <option value="closed">Closed</option>
              <option value="">Everything</option>
            </select>
          </label>
        }
      >
        {tickets.loading ? <Loading label="Loading requests…" /> : null}
        <ErrorNote error={tickets.error} title="The requests did not load" onRetry={tickets.reload} />
        <DataTable
          empty="Nothing in this queue."
          rows={listOf(tickets.data, "tickets")}
          columns={[
            {
              key: "subject",
              header: "Subject",
              render: (row) => (
                <button type="button" className="linklike" onClick={() => setOpened(row.id)}>
                  {row.subject}
                </button>
              ),
            },
            { key: "customer", header: "Customer", render: (row) => row.customer_email ?? row.email ?? "—" },
            { key: "status", header: "Status", render: (row) => <StatusBadge status={row.status} /> },
            { key: "updated_at", header: "Updated", render: (row) => formatDateTime(row.updated_at ?? row.created_at) },
          ]}
        />
      </Section>

      {opened ? (
        <Section title="Conversation">
          {thread.loading ? <Loading rows={2} /> : null}
          <ErrorNote error={thread.error} title="The conversation did not load" onRetry={thread.reload} />
          <ol className="plain thread">
            {listOf(thread.data, "messages").map((message) => (
              <li key={message.id} className={`message${message.from_staff ? " from-staff" : ""}`}>
                <p className="message-head">
                  <strong>{message.author_name ?? (message.from_staff ? "Customer service" : "Customer")}</strong>
                  <span className="muted small"> · {formatDateTime(message.created_at)}</span>
                </p>
                <p className="message-body">{message.body ?? message.text ?? ""}</p>
              </li>
            ))}
          </ol>
          <form className="card stack" onSubmit={submit}>
            <label className="field-label" htmlFor="staff-reply">Reply to the customer</label>
            <textarea
              id="staff-reply"
              className="input"
              rows={5}
              value={reply}
              onChange={(event) => setReply(event.target.value)}
              required
            />
            <ErrorNote error={send.error} title="The reply was not sent" />
            <button type="submit" className="btn btn-primary" disabled={send.pending}>
              {send.pending ? "Sending…" : "Send"}
            </button>
          </form>
        </Section>
      ) : null}
    </>
  );
}
