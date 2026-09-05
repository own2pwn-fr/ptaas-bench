<?php

declare(strict_types=1);

namespace Internal\Telemetry;

/**
 * The way out for records: one JSON object per line, appended to a local file, drained
 * by a separate process.
 *
 * WHY A SPOOL AND NOT A BACKGROUND SENDER
 * ---------------------------------------
 * The rule this agent exists under is that the collector must never be observable from
 * a served request. The other runtimes we support satisfy it the same way: hand the
 * record to a background thread and return. PHP has no such thread. A request is a
 * process that is torn down when the response ends, so "later" has to mean either
 * "just before teardown" or "in another process". There is no third option, and both
 * of them have to be considered honestly:
 *
 *   A. Send on shutdown. Under FPM this is bearable, because fastcgi_finish_request()
 *      releases the client first and the send happens on the free side of it. Under an
 *      in-process module SAPI there is no such call: a send at shutdown happens while
 *      the connection is still open, so a collector that hangs turns straight into
 *      time-to-last-byte on every response. Response times are the numbers this agent
 *      exists to report, and several of the things watching this service compare them
 *      against thresholds, so an agent that inflates them under exactly the conditions
 *      a collector is likely to be sick is worse than no agent.
 *
 *   B. Append to a local file, and let a separate long-lived process do the network.
 *      The request path then does one open, one append and one close on a local file:
 *      no name resolution, no connection, no remote timeout, and nothing whose duration
 *      depends on a service that may be down. The cost is a second process to run in
 *      the container, a file that has to be bounded, and records that survive the
 *      request only if the disk write succeeded.
 *
 * B is what this implements. The cost is real but it is bounded and local; the cost of
 * A is unbounded and remote, and it is paid by the users of the service.
 *
 * The one exception is Telemetry::outbound(), which posts immediately. Its reason is in
 * that method: it describes a name lookup that happens microseconds later, so a record
 * that waited for the next drain would arrive after the thing it explains. It is also
 * only ever called immediately before the service makes an outbound request of its own,
 * so it is not on any timing path that was quiet to begin with.
 *
 * LOCKING
 * -------
 * Appends take an exclusive lock and hold it for one write. The drain side takes the
 * same lock, reads, truncates and releases -- it never holds it across the network.
 * That keeps the worst case a request can wait for behind a lock at the length of one
 * local read of a file that is bounded by spoolMaxBytes.
 */
final class Spool
{
    private int $dropped = 0;

    private int $written = 0;

    private bool $directoryChecked = false;

    public function __construct(private readonly Config $config)
    {
    }

    /**
     * Append one record. Never raises, never blocks on anything remote.
     *
     * @param array<string,mixed> $record
     */
    public function append(array $record): void
    {
        $line = json_encode($record, JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE);
        if ($line === false) {
            $this->dropped++;

            return;
        }
        $this->write($line . "\n");
    }

    /**
     * @param list<array<string,mixed>> $records
     */
    public function appendMany(array $records): void
    {
        foreach ($records as $record) {
            $this->append($record);
        }
    }

    private function write(string $line): void
    {
        if (!$this->directoryChecked) {
            $this->directoryChecked = true;
            $directory = dirname($this->config->spoolPath);
            if (!is_dir($directory)) {
                @mkdir($directory, 0o770, true);
            }
        }
        $handle = @fopen($this->config->spoolPath, 'ab');
        if ($handle === false) {
            $this->dropped++;

            return;
        }
        try {
            $stat = @fstat($handle);
            if (is_array($stat) && isset($stat['size']) && $stat['size'] > $this->config->spoolMaxBytes) {
                // Nothing is draining. Growing a file on the container's writable layer
                // until the disk fills is how an agent takes down what it watches.
                $this->dropped++;

                return;
            }
            if (@flock($handle, LOCK_EX)) {
                @fwrite($handle, $line);
                @fflush($handle);
                @flock($handle, LOCK_UN);
                $this->written++;
            } else {
                $this->dropped++;
            }
        } finally {
            @fclose($handle);
        }
    }

    /**
     * Take everything queued and leave the file empty. Called by the drain process.
     *
     * @return list<string> raw JSON lines, in the order they were appended
     */
    public function take(): array
    {
        $path = $this->config->spoolPath;
        if (!is_file($path) || @filesize($path) === 0) {
            return [];
        }
        $handle = @fopen($path, 'c+b');
        if ($handle === false) {
            return [];
        }
        $contents = '';
        try {
            if (!@flock($handle, LOCK_EX)) {
                return [];
            }
            $contents = (string) @stream_get_contents($handle);
            @ftruncate($handle, 0);
            @fflush($handle);
            @flock($handle, LOCK_UN);
        } finally {
            @fclose($handle);
        }
        if ($contents === '') {
            return [];
        }

        return array_values(array_filter(explode("\n", $contents), static fn (string $l): bool => trim($l) !== ''));
    }

    /** @return array{written:int,dropped:int} */
    public function stats(): array
    {
        return ['written' => $this->written, 'dropped' => $this->dropped];
    }
}
