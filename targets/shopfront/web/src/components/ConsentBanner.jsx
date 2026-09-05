/**
 * Cookie choice.
 *
 * Shown until the visitor has answered once. "Reject" writes the opt-out cookie the
 * measurement snippet in /assets/insight.js checks before it queues anything, so the
 * choice takes effect without a reload.
 */
import { useState } from "react";
import { Link } from "react-router-dom";

import { readCookie, writeCookie } from "../lib/store.js";

function alreadyAnswered() {
  return Boolean(readCookie("cookie_choice") || readCookie("insight_opt_out"));
}

export default function ConsentBanner() {
  const [answered, setAnswered] = useState(alreadyAnswered);

  if (answered) return null;

  const decide = (choice) => {
    writeCookie("cookie_choice", choice);
    writeCookie("insight_opt_out", choice === "accept" ? "0" : "1");
    setAnswered(true);
  };

  return (
    <aside className="consent" role="region" aria-label="Cookie choice">
      <div className="consent-inner">
        <p className="consent-text">
          We use our own cookies to keep your basket and to count which pages get used. We do not
          share any of it with anyone else. <Link to="/pages/cookies">How we use cookies</Link>
        </p>
        <p className="consent-actions">
          <button type="button" className="btn btn-quiet" data-track="consent-reject" onClick={() => decide("reject")}>
            Reject
          </button>
          <button type="button" className="btn btn-primary" data-track="consent-accept" onClick={() => decide("accept")}>
            Accept
          </button>
        </p>
      </div>
    </aside>
  );
}
