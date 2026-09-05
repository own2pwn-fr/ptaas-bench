/**
 * Postgres access.
 *
 * One pool for the process, a `sql` helper for parameterised statements and an `unsafe`
 * helper for the handful of places that still build statement text themselves. Both go
 * through the same pool so the connection settings and the slow-query log stay in one
 * place.
 */
import pg from "pg";

import config from "./config.js";

// Money columns are INTEGER, but COUNT() and SUM() come back as BIGINT and node-postgres
// hands those to the application as strings to avoid losing precision. Every caller here
// wants a number, so the two aggregate OIDs are parsed centrally instead of at 40 call
// sites. (20 = int8, 1700 = numeric.)
pg.types.setTypeParser(20, (v) => (v === null ? null : Number(v)));
pg.types.setTypeParser(1700, (v) => (v === null ? null : Number(v)));

export const pool = new pg.Pool({
  connectionString: config.database.url,
  max: config.database.poolSize,
  idleTimeoutMillis: 30_000,
  connectionTimeoutMillis: 10_000,
});

/** Parameterised query. Everything that takes user input should come through here. */
export async function sql(text, params = []) {
  const result = await pool.query(text, params);
  return result.rows;
}

/** Parameterised query returning the first row, or null. */
export async function one(text, params = []) {
  const rows = await sql(text, params);
  return rows.length > 0 ? rows[0] : null;
}

/**
 * Statement text built by the caller, with parameters for the values that are not
 * part of the assembled fragment.
 *
 * Kept separate from `sql` so that the places which still assemble their own statements
 * are greppable: the reporting filters, the saved-search compiler and the catalogue
 * sort. Returns the full result object because those callers look at `fields` as well as
 * `rows`.
 */
export async function unsafe(text, params = []) {
  return pool.query(text, params);
}

/**
 * Allocate the next id for a table.
 *
 * Ids are handed out from a counter table rather than a sequence because the seeded
 * block has to be reproducible across estates: the reset writes the counter to the end
 * of the seeded range and everything created afterwards continues from there.
 */
export async function nextId(name) {
  const rows = await sql(
    `UPDATE id_counters SET value = value + 1 WHERE name = $1 RETURNING value`,
    [name],
  );
  if (rows.length === 0) throw new Error(`no id counter named ${name}`);
  return rows[0].value;
}

export async function close() {
  await pool.end();
}
