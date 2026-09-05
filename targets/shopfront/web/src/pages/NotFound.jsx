import { Link } from "react-router-dom";

import { EmptyState } from "../components/ui.jsx";

export default function NotFound() {
  return (
    <EmptyState
      title="We could not find that page"
      action={
        <Link className="btn btn-primary" to="/">
          Back to the shop
        </Link>
      }
    >
      The link may be old, or the product may have been discontinued. The catalogue and the help
      centre are both a click away.
    </EmptyState>
  );
}
