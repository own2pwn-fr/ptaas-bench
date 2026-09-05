-- Meridian operations console - restore the database to its reference state.
--
-- Both statements below are client side SOURCE directives, so run this file from
-- the directory that holds it:
--
--   mysql --host=db --user=root --password < reset.sql
--
-- schema.sql drops and recreates every table, seed.sql reloads the reference rows.
-- The pair is idempotent: running it twice leaves the same database as running it
-- once, which is what the nightly refresh of the staging copy relies on.

SOURCE schema.sql;
SOURCE seed.sql;

USE `meridian`;

SELECT CONCAT(
  'accounts=', (SELECT COUNT(*) FROM `accounts`),
  ' staff=', (SELECT COUNT(*) FROM `staff`),
  ' invoices=', (SELECT COUNT(*) FROM `invoices`),
  ' consignments=', (SELECT COUNT(*) FROM `consignments`),
  ' ledger_entries=', (SELECT COUNT(*) FROM `ledger_entries`),
  ' approvals=', (SELECT COUNT(*) FROM `approvals`),
  ' audit_events=', (SELECT COUNT(*) FROM `audit_events`)
) AS `reload_summary`;
