import { useEffect, useState } from "react";

import { DataTable, ErrorNote, Loading, Notice, Section } from "../../components/ui.jsx";
import { api, useAction, useResource } from "../../lib/api.js";
import { formatDateTime, listOf } from "../../lib/store.js";

const FIELDS = [
  ["first_name", "First name", "given-name"],
  ["last_name", "Last name", "family-name"],
  ["email", "E-mail", "email"],
  ["phone", "Phone", "tel"],
];

export default function Profile() {
  const profile = useResource(({ signal }) => api.get("/api/account/profile", null, { signal }), []);
  const sessions = useResource(({ signal }) => api.get("/api/account/sessions", null, { signal }), []);
  const save = useAction();
  const avatar = useAction();
  const [form, setForm] = useState({ first_name: "", last_name: "", email: "", phone: "", marketing_opt_in: false });
  const [imageUrl, setImageUrl] = useState("");
  const [notice, setNotice] = useState(null);

  useEffect(() => {
    const data = profile.data?.profile ?? profile.data?.user ?? profile.data;
    if (data) setForm((current) => ({ ...current, ...data }));
  }, [profile.data]);

  const submit = async (event) => {
    event.preventDefault();
    const done = await save.run(() =>
      api.patch("/api/account/profile", {
        first_name: form.first_name,
        last_name: form.last_name,
        email: form.email,
        phone: form.phone,
        marketing_opt_in: Boolean(form.marketing_opt_in),
      }),
    );
    if (done !== undefined) {
      setNotice("Profile saved.");
      profile.reload();
    }
  };

  /** The picture is fetched by us from the address the customer gives, then resized. */
  const importPicture = async (event) => {
    event.preventDefault();
    const done = await avatar.run(() => api.post("/api/account/avatar/import", { image_url: imageUrl.trim() }));
    if (done !== undefined) {
      setNotice("Picture updated.");
      setImageUrl("");
      profile.reload();
    }
  };

  if (profile.loading) return <Loading label="Loading your profile…" />;

  const current = profile.data?.profile ?? profile.data?.user ?? profile.data ?? {};

  return (
    <>
      <Section title="Profile" description="What we call you and how we reach you.">
        <ErrorNote error={profile.error} title="Your profile did not load" onRetry={profile.reload} />
        {notice ? <Notice tone="good">{notice}</Notice> : null}
        <form className="card stack narrow-form" onSubmit={submit}>
          {FIELDS.map(([key, label, autoComplete]) => (
            <span className="field" key={key}>
              <label className="field-label" htmlFor={`profile-${key}`}>{label}</label>
              <input
                id={`profile-${key}`}
                className="input"
                autoComplete={autoComplete}
                value={form[key] ?? ""}
                onChange={(event) => setForm({ ...form, [key]: event.target.value })}
              />
            </span>
          ))}
          <label className="choice">
            <input
              type="checkbox"
              checked={Boolean(form.marketing_opt_in)}
              onChange={(event) => setForm({ ...form, marketing_opt_in: event.target.checked })}
            />
            <span>Send me the monthly letter.</span>
          </label>
          <ErrorNote error={save.error} title="Your profile was not saved" />
          <button type="submit" className="btn btn-primary" disabled={save.pending} data-track="profile-save">
            {save.pending ? "Saving…" : "Save profile"}
          </button>
        </form>
      </Section>

      <Section title="Picture" description="Give us the address of a picture and we will fetch it.">
        <form className="card stack narrow-form" onSubmit={importPicture}>
          {current.avatar_url ? (
            <img className="avatar" src={current.avatar_url} alt="Your account picture" width="72" height="72" />
          ) : null}
          <label className="field-label" htmlFor="avatar-url">Picture address</label>
          <input
            id="avatar-url"
            className="input"
            type="url"
            placeholder="https://…"
            value={imageUrl}
            onChange={(event) => setImageUrl(event.target.value)}
            required
          />
          <ErrorNote error={avatar.error} title="We could not fetch that picture" />
          <button type="submit" className="btn btn-quiet" disabled={avatar.pending}>
            {avatar.pending ? "Fetching…" : "Use this picture"}
          </button>
        </form>
      </Section>

      <Section title="Where you are signed in">
        {sessions.loading ? <Loading rows={2} /> : null}
        <ErrorNote error={sessions.error} title="Your sessions did not load" onRetry={sessions.reload} />
        <DataTable
          empty="Only this browser."
          rows={listOf(sessions.data, "sessions")}
          columns={[
            { key: "created_at", header: "Signed in", render: (row) => formatDateTime(row.created_at) },
            { key: "last_seen_at", header: "Last used", render: (row) => formatDateTime(row.last_seen_at ?? row.updated_at) },
            { key: "user_agent", header: "Browser", render: (row) => String(row.user_agent ?? "—").slice(0, 60) },
            { key: "ip", header: "Address", render: (row) => row.ip ?? row.ip_address ?? "—" },
          ]}
        />
      </Section>
    </>
  );
}
