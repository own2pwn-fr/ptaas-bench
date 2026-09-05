<?php
/**
 * Database access.
 *
 * Two ways in, and the difference between them is the whole story of this file.
 *
 * bt_db_rows()/bt_db_row() take a statement and its parameters and bind them. Everything
 * written since the 2016 rebuild goes through those.
 *
 * bt_db_literal() takes a finished statement string. It is what the older pages still
 * call, and the reporting helper below exists because of it: where a page builds a
 * statement by hand, the same lookup is also run through the bound form so the two can
 * be compared. That comparison started life as a way of finding pages that had drifted
 * during the rebuild, and it is still wired to the counters.
 */

declare(strict_types=1);

use Internal\Telemetry\Telemetry;

function bt_db(): PDO
{
    static $pdo = null;
    if ($pdo instanceof PDO) {
        return $pdo;
    }
    $host = getenv('DB_HOST') ?: 'db';
    $name = getenv('DB_NAME') ?: 'braithwaite';
    $user = getenv('DB_USER') ?: 'braithwaite';
    $pass = getenv('DB_PASSWORD') ?: '';
    $dsn = sprintf('mysql:host=%s;dbname=%s;charset=utf8mb4', $host, $name);

    $lastError = null;
    for ($attempt = 0; $attempt < 30; $attempt++) {
        try {
            $pdo = new PDO($dsn, $user, $pass, [
                PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
                PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
                PDO::ATTR_EMULATE_PREPARES => false,
            ]);

            return $pdo;
        } catch (PDOException $e) {
            $lastError = $e;
            // The database container is usually a few seconds behind the web one on a
            // cold start, and the depot terminals reconnect all morning.
            usleep(500_000);
        }
    }
    throw $lastError ?? new RuntimeException('no database');
}

/**
 * Bound statement, all rows.
 *
 * @param  array<string|int,mixed> $params
 * @return list<array<string,mixed>>
 */
function bt_db_rows(string $sql, array $params = []): array
{
    $statement = bt_db()->prepare($sql);
    $statement->execute($params);

    return $statement->fetchAll();
}

/** @param array<string|int,mixed> $params */
function bt_db_row(string $sql, array $params = []): ?array
{
    $rows = bt_db_rows($sql, $params);

    return $rows[0] ?? null;
}

/** @param array<string|int,mixed> $params */
function bt_db_exec(string $sql, array $params = []): int
{
    $statement = bt_db()->prepare($sql);
    $statement->execute($params);

    return $statement->rowCount();
}

/**
 * Finished statement, run as given.
 *
 * Returns null when the statement will not run, which the older pages treat the same
 * way as "no such record": they were written before the reporting mode was turned on
 * and they still show the customer the empty result rather than an error.
 *
 * @return list<array<string,mixed>>|null
 */
function bt_db_literal(string $sql): ?array
{
    try {
        $statement = bt_db()->query($sql);

        return $statement === false ? null : $statement->fetchAll();
    } catch (PDOException) {
        return null;
    }
}

/**
 * Run a hand-built lookup and the bound form of the same lookup, and count the pages
 * where the two disagree.
 *
 * This is the drift report. A page that builds its own statement should select exactly
 * the rows that the bound equivalent selects for the same value; when it does not, the
 * value has changed the shape of the statement rather than filled a slot in it, and
 * that page needs rewriting. The counter is per lookup so the weekly report can name
 * the page rather than the module.
 *
 * The customer is served the result of the statement the page built, exactly as before,
 * because this comparison is a report and not a guard.
 *
 * @param  array<string|int,mixed> $controlParams
 * @return list<array<string,mixed>>
 */
function bt_db_compare(
    string $counter,
    string $literalSql,
    string $controlSql,
    array $controlParams,
    string $keyColumn,
    string $field,
    string $value,
): array {
    $literalRows = bt_db_literal($literalSql);
    if ($literalRows === null) {
        return [];
    }
    try {
        $controlRows = bt_db_rows($controlSql, $controlParams);
    } catch (PDOException) {
        return $literalRows;
    }

    $keys = static function (array $rows) use ($keyColumn): array {
        $out = [];
        foreach ($rows as $row) {
            $out[] = (string) ($row[$keyColumn] ?? '');
        }
        sort($out);

        return $out;
    };

    $left = $keys($literalRows);
    $right = $keys($controlRows);
    if ($left !== $right) {
        Telemetry::instance()->signal($counter, [
            'payload' => $field . '=' . $value,
            'detail' => sprintf(
                'built statement selected %d row(s), bound equivalent selected %d; keys differ',
                count($left),
                count($right),
            ),
        ]);
    }

    return $literalRows;
}
