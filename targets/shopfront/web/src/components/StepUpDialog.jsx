/**
 * Second confirmation for money movements.
 *
 * Stored cards, wallet withdrawals and order confirmation all ask for the six-digit code
 * we send to the account's phone number. The dialog owns the whole exchange: request,
 * verify, resend.
 */
import { useEffect, useState } from "react";

import { api, useAction } from "../lib/api.js";
import { ErrorNote, Loading } from "./ui.jsx";

export default function StepUpDialog({ purpose, title = "Confirm it is you", onDone, onCancel }) {
  const [requestId, setRequestId] = useState(null);
  const [starting, setStarting] = useState(true);
  const [startError, setStartError] = useState(null);
  const [code, setCode] = useState("");
  const [sentAgain, setSentAgain] = useState(false);
  const { pending, error, run } = useAction();

  useEffect(() => {
    let live = true;
    api
      .post("/api/auth/step-up/requests", { purpose })
      .then((data) => {
        if (!live) return;
        setRequestId(data?.step_up_id ?? data?.id ?? null);
        setStarting(false);
      })
      .catch((caught) => {
        if (!live) return;
        setStartError(caught);
        setStarting(false);
      });
    return () => {
      live = false;
    };
  }, [purpose]);

  const submit = async (event) => {
    event.preventDefault();
    const result = await run(() =>
      api.post("/api/auth/step-up/verify", { step_up_id: requestId, code: code.trim() }),
    );
    if (result !== undefined) onDone?.(result);
  };

  const resend = () =>
    run(async () => {
      await api.post("/api/auth/step-up/resend", { step_up_id: requestId });
      setSentAgain(true);
    });

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label={title}>
      <div className="modal">
        <h2 className="modal-title">{title}</h2>
        <p className="muted">
          We have sent a six-digit code to the phone number on the account. It is good for ten
          minutes.
        </p>
        {starting ? <Loading label="Sending the code…" rows={1} /> : null}
        <ErrorNote error={startError} title="We could not send a code" />
        {!starting && !startError ? (
          <form className="stack" onSubmit={submit}>
            <label className="field-label" htmlFor="step-up-code">
              Code
            </label>
            <input
              id="step-up-code"
              className="input"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={8}
              value={code}
              onChange={(event) => setCode(event.target.value)}
              required
            />
            <ErrorNote error={error} title="That code was not accepted" />
            {sentAgain ? <p className="muted small">A new code is on its way.</p> : null}
            <div className="row gap">
              <button type="submit" className="btn btn-primary" disabled={pending || !requestId}>
                {pending ? "Checking…" : "Confirm"}
              </button>
              <button type="button" className="btn btn-quiet" onClick={resend} disabled={pending || !requestId}>
                Send it again
              </button>
              <button type="button" className="btn btn-quiet" onClick={onCancel}>
                Cancel
              </button>
            </div>
          </form>
        ) : null}
      </div>
    </div>
  );
}
