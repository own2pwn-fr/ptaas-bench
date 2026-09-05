import { DataTable, ErrorNote, Loading, Section } from "../../components/ui.jsx";
import { api, useResource } from "../../lib/api.js";
import { formatDate, humanise, listOf } from "../../lib/store.js";

export default function Loyalty() {
  const loyalty = useResource(({ signal }) => api.get("/api/account/loyalty", null, { signal }), []);
  const history = useResource(({ signal }) => api.get("/api/account/loyalty/transactions", null, { signal }), []);

  const summary = loyalty.data?.loyalty ?? loyalty.data ?? {};
  const points = summary.points ?? summary.balance ?? 0;
  const tier = summary.tier ?? summary.level;
  const toNext = summary.points_to_next_tier ?? summary.next_tier_points;

  return (
    <>
      <Section title="Rewards" description="One point per pound. A hundred points is five pounds off.">
        {loyalty.loading ? <Loading rows={1} /> : null}
        <ErrorNote error={loyalty.error} title="Your rewards did not load" onRetry={loyalty.reload} />
        <div className="grid two">
          <article className="card">
            <p className="stat">{Number(points) || 0}</p>
            <p className="muted small">points available</p>
          </article>
          <article className="card">
            <p className="stat">{tier ? humanise(tier) : "—"}</p>
            <p className="muted small">
              {toNext ? `${toNext} points to the next tier` : "your current tier"}
            </p>
          </article>
        </div>
      </Section>

      <Section title="How you earned them">
        {history.loading ? <Loading rows={2} /> : null}
        <ErrorNote error={history.error} title="The history did not load" onRetry={history.reload} />
        <DataTable
          empty="No points movements yet."
          rows={listOf(history.data, "transactions")}
          columns={[
            { key: "created_at", header: "When", render: (row) => formatDate(row.created_at) },
            { key: "reason", header: "Why", render: (row) => humanise(row.reason ?? row.kind ?? row.description) },
            { key: "order_reference", header: "Order", render: (row) => row.order_reference ?? "—" },
            {
              key: "points",
              header: "Points",
              render: (row) => `${(row.points ?? 0) > 0 ? "+" : ""}${row.points ?? 0}`,
            },
          ]}
        />
      </Section>
    </>
  );
}
