import { useState } from "react";
import { Link } from "react-router-dom";

import { DataTable, ErrorNote, Loading, Notice, Section, StatusBadge } from "../components/ui.jsx";
import { api, useAction, useResource } from "../lib/api.js";
import { useSession } from "../lib/session.jsx";
import { formatDate, listOf } from "../lib/store.js";

function NewTicket({ onCreated }) {
  const [form, setForm] = useState({ subject: "", body: "", order_reference: "" });
  const { pending, error, run } = useAction();
  const [done, setDone] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    const result = await run(() => api.post("/api/support/tickets", form));
    if (result !== undefined) {
      setDone(true);
      setForm({ subject: "", body: "", order_reference: "" });
      onCreated?.();
    }
  };

  return (
    <form className="card stack" onSubmit={submit}>
      <h3 className="card-title">Ask us something</h3>
      {done ? <Notice tone="good">We have your message. We answer within one working day.</Notice> : null}
      <label className="field-label" htmlFor="ticket-subject">Subject</label>
      <input
        id="ticket-subject"
        className="input"
        value={form.subject}
        maxLength={140}
        onChange={(event) => setForm({ ...form, subject: event.target.value })}
        required
      />
      <label className="field-label" htmlFor="ticket-order">Order number (optional)</label>
      <input
        id="ticket-order"
        className="input"
        value={form.order_reference}
        onChange={(event) => setForm({ ...form, order_reference: event.target.value })}
      />
      <label className="field-label" htmlFor="ticket-body">What has happened?</label>
      <textarea
        id="ticket-body"
        className="input"
        rows={6}
        value={form.body}
        onChange={(event) => setForm({ ...form, body: event.target.value })}
        required
      />
      <ErrorNote error={error} title="We could not open that request" />
      <button type="submit" className="btn btn-primary" disabled={pending} data-track="ticket-create">
        {pending ? "Sending…" : "Send"}
      </button>
    </form>
  );
}

export default function Support() {
  const { signedIn } = useSession();
  const articles = useResource(({ signal }) => api.get("/api/support/articles", null, { signal }), []);
  const faq = useResource(({ signal }) => api.get("/api/content/faq", null, { signal }), []);
  const tickets = useResource(({ signal }) => api.get("/api/support/tickets", null, { signal }), [], {
    skip: !signedIn,
  });

  return (
    <>
      <Section title="Help centre" description="Answers to the things we get asked most.">
        {articles.loading ? <Loading rows={2} /> : null}
        <ErrorNote error={articles.error} title="The help articles did not load" onRetry={articles.reload} />
        <div className="grid article-grid">
          {listOf(articles.data, "articles").map((article) => (
            <article className="card" key={article.id ?? article.slug}>
              <h3 className="card-title">{article.title}</h3>
              <p className="muted small">{article.summary ?? article.excerpt ?? ""}</p>
              {article.slug ? <Link to={`/pages/${encodeURIComponent(article.slug)}`}>Read it</Link> : null}
            </article>
          ))}
        </div>
      </Section>

      <Section title="Frequently asked">
        <ErrorNote error={faq.error} title="The questions did not load" onRetry={faq.reload} />
        <dl className="faq">
          {listOf(faq.data, "faq", "questions", "entries").map((entry, index) => (
            <div className="faq-entry" key={entry.id ?? index}>
              <dt>{entry.question ?? entry.title}</dt>
              <dd>{entry.answer ?? entry.body}</dd>
            </div>
          ))}
        </dl>
      </Section>

      <Section title="Your requests">
        {!signedIn ? (
          <p className="muted">
            <Link to="/sign-in">Sign in</Link> to see the requests you have opened, or call us on
            +44 20 7946 0311.
          </p>
        ) : (
          <>
            {tickets.loading ? <Loading rows={2} /> : null}
            <ErrorNote error={tickets.error} title="Your requests did not load" onRetry={tickets.reload} />
            <DataTable
              empty="You have not asked us anything yet."
              rows={listOf(tickets.data, "tickets")}
              columns={[
                {
                  key: "subject",
                  header: "Subject",
                  render: (row) => <Link to={`/support/tickets/${encodeURIComponent(row.id)}`}>{row.subject}</Link>,
                },
                { key: "status", header: "Status", render: (row) => <StatusBadge status={row.status} /> },
                { key: "updated_at", header: "Updated", render: (row) => formatDate(row.updated_at ?? row.created_at) },
              ]}
            />
            <NewTicket onCreated={tickets.reload} />
          </>
        )}
      </Section>
    </>
  );
}
