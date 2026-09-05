import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { ErrorNote, Section } from "../components/ui.jsx";
import { useAction } from "../lib/api.js";
import { useSession } from "../lib/session.jsx";
import { storeName } from "../lib/store.js";

export default function SignIn() {
  const { signIn } = useSession();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [form, setForm] = useState({ email: "", password: "" });
  const { pending, error, run } = useAction();

  const submit = async (event) => {
    event.preventDefault();
    const done = await run(() => signIn(form.email, form.password));
    if (done !== undefined) {
      const next = params.get("next");
      navigate(next && next.startsWith("/") ? next : "/account");
    }
  };

  return (
    <Section title="Sign in">
      <form className="card stack narrow-form" onSubmit={submit}>
        <p className="muted">Your {storeName()} account: orders, addresses, wallet and repairs.</p>
        <label className="field-label" htmlFor="email">E-mail</label>
        <input
          id="email"
          className="input"
          type="email"
          autoComplete="email"
          value={form.email}
          onChange={(event) => setForm({ ...form, email: event.target.value })}
          required
        />
        <label className="field-label" htmlFor="password">Password</label>
        <input
          id="password"
          className="input"
          type="password"
          autoComplete="current-password"
          value={form.password}
          onChange={(event) => setForm({ ...form, password: event.target.value })}
          required
        />
        <ErrorNote error={error} title="We could not sign you in" />
        <button type="submit" className="btn btn-primary" disabled={pending} data-track="sign-in">
          {pending ? "Signing in…" : "Sign in"}
        </button>
        <p className="muted small">
          No account yet? <Link to="/sign-up">Create one</Link>.
        </p>
      </form>
    </Section>
  );
}
