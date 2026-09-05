<?php
/**
 * Exit 0 when the trading database has not been built yet, 1 when it has.
 *
 * Used by the container entry point, which must build a new database on a cold start
 * and must not touch one that is already there.
 */

declare(strict_types=1);

$host = getenv('DB_HOST') ?: 'db';
$name = getenv('DB_NAME') ?: 'braithwaite';
$user = getenv('DB_USER') ?: 'braithwaite';
$pass = getenv('DB_PASSWORD') ?: '';

for ($attempt = 0; $attempt < 90; $attempt++) {
    try {
        $pdo = new PDO(
            sprintf('mysql:host=%s;dbname=%s;charset=utf8mb4', $host, $name),
            $user,
            $pass,
            [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION],
        );
        $found = $pdo->query("SHOW TABLES LIKE 'products'")->fetchColumn();
        exit($found === false ? 0 : 1);
    } catch (PDOException) {
        sleep(1);
    }
}

fwrite(STDERR, "the database did not answer\n");
exit(1);
