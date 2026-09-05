/** Previous / next with a page readout. Server pages are 1-based. */
export default function Pagination({ page, pageCount, total, onChange }) {
  const current = Number(page) || 1;
  const last = Number(pageCount) || 1;
  if (last <= 1) return null;
  return (
    <nav className="pagination" aria-label="Pages">
      <button type="button" className="btn btn-quiet" disabled={current <= 1} onClick={() => onChange(current - 1)}>
        Previous
      </button>
      <span className="pagination-state">
        Page {current} of {last}
        {total ? ` · ${total} items` : ""}
      </span>
      <button type="button" className="btn btn-quiet" disabled={current >= last} onClick={() => onChange(current + 1)}>
        Next
      </button>
    </nav>
  );
}
