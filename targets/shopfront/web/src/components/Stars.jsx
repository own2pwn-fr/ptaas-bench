/** Five-mark rating, rounded to the nearest half for display only. */
export default function Stars({ value, count }) {
  const rating = Number(value);
  if (!Number.isFinite(rating) || rating <= 0) return <span className="stars muted small">No reviews yet</span>;
  const filled = Math.round(rating * 2) / 2;
  return (
    <span className="stars" title={`${rating.toFixed(1)} out of 5`}>
      <span className="stars-marks" aria-hidden="true">
        {[1, 2, 3, 4, 5].map((step) => (
          <span key={step} className={step <= filled ? "mark on" : "mark"}>
            ★
          </span>
        ))}
      </span>
      <span className="stars-text">
        {rating.toFixed(1)}
        {count ? ` (${count})` : ""}
      </span>
    </span>
  );
}
