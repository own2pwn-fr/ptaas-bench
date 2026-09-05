/**
 * Query-plan checks for the statements the service still assembles as text.
 *
 * Three code paths build SQL by hand: the catalogue filter, the GraphQL product resolver
 * and the saved-search compiler. All three are on the roadmap to move onto the query
 * builder. Until then they are watched, because a filter fragment that widens the plan
 * beyond the table it was written for is the failure mode that matters — and it is a
 * property of the statement and its result, not of the text a customer typed.
 */

/**
 * Did the assembled statement pick up structure the template does not have?
 *
 * This is only the cheap pre-check that decides whether it is worth confirming the
 * result set; on its own it says nothing, because customers legitimately search for
 * "union jack" and for apostrophes.
 */
export function statementWidened(statement, template) {
  const added = String(statement).length - String(template).length;
  if (added <= 0) return false;
  const s = String(statement);
  return (
    /\bunion\b[\s\S]{0,40}\bselect\b/i.test(s) ||
    /'\s*(?:or|and)\s+'?\d+'?\s*=\s*'?\d+/i.test(s) ||
    /;\s*(?:select|insert|update|delete|drop|copy)\b/i.test(s) ||
    /\bfrom\s+(?:pg_|information_schema\.)/i.test(s) ||
    /\b(?:pg_sleep|pg_read_file|current_setting|version)\s*\(/i.test(s)
  );
}

/**
 * Confirm against the result.
 *
 * `expected` is the set of primary keys the parameterised equivalent of the same filter
 * returns. Anything outside it came from somewhere the statement was not written to
 * read, which is the condition worth counting.
 */
export function resultEscaped(rows, expected, idColumn = "id") {
  if (!Array.isArray(rows) || rows.length === 0) return null;
  const allowed = new Set((expected ?? []).map((v) => String(v)));
  const foreign = [];
  for (const row of rows) {
    const key = String(row?.[idColumn]);
    if (!allowed.has(key)) foreign.push(key);
  }
  if (foreign.length === 0 && rows.length <= allowed.size) return null;
  return {
    returned: rows.length,
    expected: allowed.size,
    foreign: foreign.slice(0, 8),
    columns: Object.keys(rows[0] ?? {}),
  };
}

/**
 * Turn a driver error into the diagnostic the reporting UI shows.
 *
 * Only the catalogue sort path serves this to a client: it was added when the analysts
 * asked to see why their saved column list stopped working, and it is the one place in
 * the API where a database message reaches the browser.
 */
export function describeDriverError(error) {
  return {
    message: String(error?.message ?? error),
    code: error?.code ?? null,
    position: error?.position ?? null,
    routine: error?.routine ?? null,
    statement: error?.internalQuery ?? null,
  };
}
