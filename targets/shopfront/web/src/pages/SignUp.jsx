import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { ErrorNote, Section } from "../components/ui.jsx";
import { useAction } from "../lib/api.js";
import { useSession } from "../lib/session.jsx";

const FIELDS = [
  ["first_name", "First name", "given-name", "text"],
  ["last_name", "Last name", "family-name", "text"],
  ["email", "E-mail", "email", "email"],
  ["password", "Password", "new-password", "password"],
];

export default function SignUp() {
  const { register } = useSession();
  const navigate = useNavigate();
  const [form, setForm] = useState({ first_name: "", last_name: "", email: "", password: "", marketing_opt_in: false });
  const { pending, error, run } = useAction();

  const submit = async (event) => {
    event.preventDefault();
    const done = await run(() => register(form));
    if (done !== undefined) navigate("/account");
  };

  const fieldError = error?.fieldErrors ?? {};

  return (
    <Section title="Create an account">
      <form className="card stack narrow-form" onSubmit={submit}>
        {FIELDS.map(([key, label, autoComplete, type]) => (
          <span className="field" key={key}>
            <label className="field-label" htmlFor={key}>{label}</label>
            <input
              id={key}
              className="input"
              type={type}
              autoComplete={autoComplete}
              value={form[key]}
              onChange={(event) => setForm({ ...form, [key]: event.target.value })}
              required
            />
            {fieldError[key] ? <span className="field-error">{String(fieldError[key])}</span> : null}
          </span>
        ))}
        <label className="choice">
          <input
            type="checkbox"
            checked={form.marketing_opt_in}
            onChange={(event) => setForm({ ...form, marketing_opt_in: event.target.checked })}
          />
          <span>Send me the monthly letter: new stock, repair guides, nothing else.</span>
        </label>
        <ErrorNote error={error} title="We could not create the account" />
        <button type="submit" className="btn btn-primary" disabled={pending} data-track="sign-up">
          {pending ? "Creating…" : "Create account"}
        </button>
        <p className="muted small">
          Already with us? <Link to="/sign-in">Sign in</Link>.
        </p>
      </form>
    </Section>
  );
}
