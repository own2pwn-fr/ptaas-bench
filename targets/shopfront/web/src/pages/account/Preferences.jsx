/**
 * Preferences, including the panel editor for the overview page.
 *
 * The whole preferences document is written back in one PUT: the panel list is ordered,
 * and sending it whole is the only way the order survives a rename.
 */
import { useEffect, useState } from "react";

import { ErrorNote, Loading, Notice, Section } from "../../components/ui.jsx";
import { api, useAction, useResource } from "../../lib/api.js";
import { listOf } from "../../lib/store.js";

const SIZES = ["small", "medium", "large"];

function newWidget(index) {
  return { id: `panel-${index + 1}-${Math.random().toString(36).slice(2, 6)}`, title: "New panel", size: "medium" };
}

export default function Preferences() {
  const preferences = useResource(({ signal }) => api.get("/api/account/preferences", null, { signal }), []);
  const currencies = useResource(({ signal }) => api.get("/api/currencies", null, { signal }), []);
  const locales = useResource(({ signal }) => api.get("/api/locales", null, { signal }), []);
  const save = useAction();
  const [saved, setSaved] = useState(false);
  const [draft, setDraft] = useState({ widgets: [], locale: "", currency: "", theme: "light" });

  useEffect(() => {
    const settings = preferences.data?.preferences ?? preferences.data;
    if (!settings) return;
    setDraft({
      widgets: Array.isArray(settings.widgets) ? settings.widgets.map((w) => ({ ...w })) : [],
      locale: settings.locale ?? "",
      currency: settings.currency ?? "",
      theme: settings.theme ?? "light",
    });
  }, [preferences.data]);

  const patchWidget = (index, patch) =>
    setDraft((current) => ({
      ...current,
      widgets: current.widgets.map((widget, i) => (i === index ? { ...widget, ...patch } : widget)),
    }));

  const moveWidget = (index, delta) =>
    setDraft((current) => {
      const widgets = [...current.widgets];
      const target = index + delta;
      if (target < 0 || target >= widgets.length) return current;
      [widgets[index], widgets[target]] = [widgets[target], widgets[index]];
      return { ...current, widgets };
    });

  const submit = async (event) => {
    event.preventDefault();
    const done = await save.run(() =>
      api.put("/api/account/preferences", {
        widgets: draft.widgets,
        locale: draft.locale,
        currency: draft.currency,
        theme: draft.theme,
      }),
    );
    if (done !== undefined) {
      setSaved(true);
      preferences.reload();
    }
  };

  if (preferences.loading) return <Loading label="Loading your preferences…" />;

  return (
    <form onSubmit={submit}>
      <Section title="Preferences" description="Language, currency and how the overview page is laid out.">
        <ErrorNote error={preferences.error} title="Your preferences did not load" onRetry={preferences.reload} />
        {saved ? <Notice tone="good">Saved.</Notice> : null}

        <div className="grid two">
          <span className="field">
            <label className="field-label" htmlFor="pref-locale">Language</label>
            <select
              id="pref-locale"
              className="input"
              value={draft.locale}
              onChange={(event) => setDraft({ ...draft, locale: event.target.value })}
            >
              <option value="">Use the shop default</option>
              {listOf(locales.data, "locales").map((locale) => {
                const code = typeof locale === "string" ? locale : (locale.code ?? locale.tag);
                return (
                  <option key={code} value={code}>
                    {typeof locale === "string" ? locale : (locale.name ?? code)}
                  </option>
                );
              })}
            </select>
          </span>

          <span className="field">
            <label className="field-label" htmlFor="pref-currency">Currency</label>
            <select
              id="pref-currency"
              className="input"
              value={draft.currency}
              onChange={(event) => setDraft({ ...draft, currency: event.target.value })}
            >
              <option value="">Use the shop default</option>
              {listOf(currencies.data, "currencies").map((currency) => {
                const code = typeof currency === "string" ? currency : (currency.code ?? currency.id);
                return (
                  <option key={code} value={code}>
                    {typeof currency === "string" ? currency : (currency.name ?? code)}
                  </option>
                );
              })}
            </select>
          </span>
        </div>

        <span className="field">
          <label className="field-label" htmlFor="pref-theme">Appearance</label>
          <select
            id="pref-theme"
            className="input"
            value={draft.theme}
            onChange={(event) => setDraft({ ...draft, theme: event.target.value })}
          >
            <option value="light">Light</option>
            <option value="dark">Dark</option>
            <option value="system">Match my device</option>
          </select>
        </span>
      </Section>

      <Section
        title="Overview panels"
        description="Name each panel however you like — bold and italic are allowed in the title."
        actions={
          <button
            type="button"
            className="btn btn-quiet"
            onClick={() => setDraft({ ...draft, widgets: [...draft.widgets, newWidget(draft.widgets.length)] })}
          >
            Add a panel
          </button>
        }
      >
        <ul className="plain widget-editor">
          {draft.widgets.map((widget, index) => (
            <li className="card" key={widget.id ?? index}>
              <div className="row gap">
                <span className="field grow">
                  <label className="field-label" htmlFor={`widget-title-${index}`}>Title</label>
                  <input
                    id={`widget-title-${index}`}
                    className="input"
                    value={widget.title ?? ""}
                    onChange={(event) => patchWidget(index, { title: event.target.value })}
                  />
                </span>
                <span className="field">
                  <label className="field-label" htmlFor={`widget-size-${index}`}>Size</label>
                  <select
                    id={`widget-size-${index}`}
                    className="input"
                    value={widget.size ?? "medium"}
                    onChange={(event) => patchWidget(index, { size: event.target.value })}
                  >
                    {SIZES.map((size) => (
                      <option key={size} value={size}>{size}</option>
                    ))}
                  </select>
                </span>
              </div>
              <p className="row gap">
                <button type="button" className="linklike" onClick={() => moveWidget(index, -1)}>Move up</button>
                <button type="button" className="linklike" onClick={() => moveWidget(index, 1)}>Move down</button>
                <button
                  type="button"
                  className="linklike"
                  onClick={() =>
                    setDraft({ ...draft, widgets: draft.widgets.filter((_, i) => i !== index) })
                  }
                >
                  Remove
                </button>
              </p>
            </li>
          ))}
          {draft.widgets.length === 0 ? <li className="muted">No panels yet.</li> : null}
        </ul>

        <ErrorNote error={save.error} title="Your preferences were not saved" />
        <button type="submit" className="btn btn-primary" disabled={save.pending} data-track="preferences-save">
          {save.pending ? "Saving…" : "Save preferences"}
        </button>
      </Section>
    </form>
  );
}
