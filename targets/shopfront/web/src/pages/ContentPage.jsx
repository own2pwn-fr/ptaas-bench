import { useParams } from "react-router-dom";

import { ErrorNote, Loading } from "../components/ui.jsx";
import { api, useResource } from "../lib/api.js";
import { formatDate } from "../lib/store.js";

/** Editorial pages: delivery, returns, terms, and the rest of the footer. */
export default function ContentPage() {
  const { slug } = useParams();
  const page = useResource(
    ({ signal }) => api.get(`/api/content/pages/${encodeURIComponent(slug)}`, null, { signal }),
    [slug],
  );

  if (page.loading) return <Loading label="Loading the page…" />;
  if (page.error) return <ErrorNote error={page.error} title="That page did not load" onRetry={page.reload} />;

  const content = page.data?.page ?? page.data;
  if (!content) return <p className="muted">We could not find that page.</p>;

  const body = String(content.body ?? content.content ?? "");
  const paragraphs = body.split(/\n{2,}/).filter(Boolean);

  return (
    <article className="prose">
      <h1>{content.title ?? content.heading}</h1>
      {content.updated_at ? (
        <p className="muted small">Last updated {formatDate(content.updated_at)}</p>
      ) : null}
      {content.summary ? <p className="lede">{content.summary}</p> : null}
      {paragraphs.map((paragraph, index) => (
        <p key={index}>{paragraph}</p>
      ))}
      {paragraphs.length === 0 ? <p className="muted">This page has no content yet.</p> : null}
    </article>
  );
}
