/** Header search with type-ahead suggestions from /api/search/suggestions. */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../lib/api.js";
import { listOf } from "../lib/store.js";

export default function SearchBox() {
  const [term, setTerm] = useState("");
  const [suggestions, setSuggestions] = useState([]);
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    const query = term.trim();
    if (query.length < 2) {
      setSuggestions([]);
      return undefined;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      api
        .get("/api/search/suggestions", { q: query }, { signal: controller.signal })
        .then((data) => setSuggestions(listOf(data, "suggestions").slice(0, 8)))
        .catch(() => setSuggestions([]));
    }, 180);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [term]);

  const go = (value) => {
    const query = String(value ?? term).trim();
    if (!query) return;
    setOpen(false);
    navigate(`/search?q=${encodeURIComponent(query)}`);
  };

  return (
    <form
      className="searchbox"
      role="search"
      onSubmit={(event) => {
        event.preventDefault();
        go();
      }}
    >
      <input
        className="input searchbox-input"
        type="search"
        name="q"
        placeholder="Search the shop"
        aria-label="Search the shop"
        value={term}
        onChange={(event) => {
          setTerm(event.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
      />
      <button type="submit" className="btn btn-primary" data-track="search-submit">
        Search
      </button>
      {open && suggestions.length ? (
        <ul className="suggestions">
          {suggestions.map((item, index) => {
            const label = typeof item === "string" ? item : (item.term ?? item.title ?? item.name ?? "");
            return (
              <li key={`${label}-${index}`}>
                <button type="button" className="suggestion" onMouseDown={() => go(label)}>
                  {label}
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}
    </form>
  );
}
