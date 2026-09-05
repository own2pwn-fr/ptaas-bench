import { Link } from "react-router-dom";

import { ErrorNote, Loading, Section, StatusBadge } from "../../components/ui.jsx";
import { api, useResource } from "../../lib/api.js";
import { formatDate, listOf, money } from "../../lib/store.js";

export default function Dashboard() {
  const preferences = useResource(({ signal }) => api.get("/api/account/preferences", null, { signal }), []);
  const orders = useResource(({ signal }) => api.get("/api/account/orders", { limit: 3 }, { signal }), []);
  const loyalty = useResource(({ signal }) => api.get("/api/account/loyalty", null, { signal }), []);
  const notifications = useResource(({ signal }) => api.get("/api/account/notifications", null, { signal }), []);

  const settings = preferences.data?.preferences ?? preferences.data ?? {};
  const widgets = Array.isArray(settings.widgets) ? settings.widgets : [];
  const recent = listOf(orders.data, "orders");
  const points = loyalty.data?.points ?? loyalty.data?.balance ?? loyalty.data?.loyalty?.points;

  return (
    <>
      <Section
        title="Overview"
        description="The panels you have put on this page."
        actions={<Link className="btn btn-quiet" to="/account/preferences">Arrange panels</Link>}
      >
        {preferences.loading ? <Loading rows={2} /> : null}
        <ErrorNote error={preferences.error} title="Your panels did not load" onRetry={preferences.reload} />
        <div className="grid widget-grid">
          {widgets.map((widget) => (
            <article className={`card widget widget-${widget.size ?? "medium"}`} key={widget.id}>
              {/*
                Panel titles belong to the customer: the panel editor offers bold, italic
                and a coloured label, so the saved title is placed as markup.
              */}
              <h3 className="widget-title" dangerouslySetInnerHTML={{ __html: widget.title ?? "" }} />
              <p className="muted small">{widget.id}</p>
            </article>
          ))}
          {!preferences.loading && widgets.length === 0 ? (
            <p className="muted">
              No panels yet. <Link to="/account/preferences">Add one</Link>.
            </p>
          ) : null}
        </div>
      </Section>

      <Section title="Latest orders" actions={<Link className="btn btn-quiet" to="/account/orders">All orders</Link>}>
        {orders.loading ? <Loading rows={2} /> : null}
        <ErrorNote error={orders.error} title="Your orders did not load" onRetry={orders.reload} />
        <ul className="plain summary-list">
          {recent.map((order) => (
            <li key={order.id}>
              <Link to={`/account/orders/${encodeURIComponent(order.id)}`}>
                {order.reference ?? `Order ${order.id}`}
              </Link>
              <span className="muted small"> · {formatDate(order.placed_at ?? order.created_at)}</span>{" "}
              <StatusBadge status={order.status} />
              <span className="right">{money(order.total_cents)}</span>
            </li>
          ))}
          {!orders.loading && recent.length === 0 ? <li className="muted">Nothing ordered yet.</li> : null}
        </ul>
      </Section>

      <div className="grid two">
        <Section title="Rewards">
          <ErrorNote error={loyalty.error} title="Your rewards did not load" onRetry={loyalty.reload} />
          <p className="stat">{Number.isFinite(Number(points)) ? Number(points) : "—"}</p>
          <p className="muted small">
            points · <Link to="/account/loyalty">see how they were earned</Link>
          </p>
        </Section>

        <Section title="Recent messages">
          <ErrorNote error={notifications.error} title="Messages did not load" onRetry={notifications.reload} />
          <ul className="plain summary-list">
            {listOf(notifications.data, "notifications").slice(0, 5).map((entry, index) => (
              <li key={entry.id ?? index}>
                {entry.title ?? entry.subject ?? entry.message}
                <span className="muted small"> · {formatDate(entry.created_at)}</span>
              </li>
            ))}
            {!notifications.loading && listOf(notifications.data, "notifications").length === 0 ? (
              <li className="muted">Nothing new.</li>
            ) : null}
          </ul>
        </Section>
      </div>
    </>
  );
}
