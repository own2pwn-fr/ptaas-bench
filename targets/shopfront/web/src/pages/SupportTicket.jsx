import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ErrorNote, Loading, Section, StatusBadge } from "../components/ui.jsx";
import { api, useAction, useResource } from "../lib/api.js";
import { formatDateTime, listOf } from "../lib/store.js";

export default function SupportTicket() {
  const { id } = useParams();
  const [reply, setReply] = useState("");
  const send = useAction();

  const ticket = useResource(
    ({ signal }) => api.get(`/api/support/tickets/${encodeURIComponent(id)}`, null, { signal }),
    [id],
  );
  const messages = useResource(
    ({ signal }) => api.get(`/api/support/tickets/${encodeURIComponent(id)}/messages`, null, { signal }),
    [id],
  );

  const submit = async (event) => {
    event.preventDefault();
    const done = await send.run(() =>
      api.post(`/api/support/tickets/${encodeURIComponent(id)}/messages`, { body: reply }),
    );
    if (done !== undefined) {
      setReply("");
      messages.reload();
      ticket.reload();
    }
  };

  if (ticket.loading) return <Loading label="Loading the conversation…" />;
  if (ticket.error) return <ErrorNote error={ticket.error} title="That request did not load" onRetry={ticket.reload} />;

  const item = ticket.data?.ticket ?? ticket.data;
  const thread = listOf(messages.data, "messages");

  return (
    <>
      <p className="crumbs">
        <Link to="/support">Help centre</Link>
        <span aria-hidden="true"> / </span>
        <span>Request {item?.reference ?? id}</span>
      </p>

      <Section
        title={item?.subject ?? "Your request"}
        description={item?.order_reference ? `About order ${item.order_reference}` : undefined}
        actions={<StatusBadge status={item?.status} />}
      >
        {messages.loading ? <Loading rows={2} /> : null}
        <ErrorNote error={messages.error} title="The messages did not load" onRetry={messages.reload} />

        <ol className="plain thread">
          {thread.map((message) => (
            <li
              key={message.id}
              className={`message${message.author_role === "agent" || message.from_staff ? " from-staff" : ""}`}
            >
              <p className="message-head">
                <strong>{message.author_name ?? (message.from_staff ? "Customer service" : "You")}</strong>
                <span className="muted small"> · {formatDateTime(message.created_at)}</span>
              </p>
              {/* Both sides of the conversation are written by people; shown as text. */}
              <p className="message-body">{message.body ?? message.text ?? ""}</p>
            </li>
          ))}
          {!messages.loading && thread.length === 0 ? (
            <li className="muted">No messages on this request yet.</li>
          ) : null}
        </ol>

        <form className="card stack" onSubmit={submit}>
          <label className="field-label" htmlFor="reply">Reply</label>
          <textarea
            id="reply"
            className="input"
            rows={5}
            value={reply}
            onChange={(event) => setReply(event.target.value)}
            required
          />
          <ErrorNote error={send.error} title="Your reply was not sent" />
          <button type="submit" className="btn btn-primary" disabled={send.pending} data-track="ticket-reply">
            {send.pending ? "Sending…" : "Send reply"}
          </button>
        </form>
      </Section>
    </>
  );
}
