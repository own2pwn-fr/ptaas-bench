<?php
/**
 * The parts of the site that talk to the operating system: shelling out, writing files,
 * queueing mail, and streaming a document back to the customer.
 *
 * Each one carries the same kind of estate counter as the database and template
 * helpers: it moves when what actually happened is not what the call site described.
 */

declare(strict_types=1);

use Internal\Telemetry\Telemetry;

/**
 * Run a report command twice and show the older pane.
 *
 * The pages that use this have two panes, added a year apart. The newer one passes the
 * argument quoted; the older one -- the one that is displayed -- does not. Running both
 * is how the difference between them is reported: when the two panes disagree, the
 * value the customer typed did something other than name the thing the command was
 * meant to act on, and the counter names the page.
 *
 * @param  string $template a command with one %s where the value goes
 * @return array{shown:string,quoted:string,changed:bool}
 */
function bt_report_command(string $counter, string $field, string $template, string $value): array
{
    $shown = (string) @shell_exec(sprintf($template, $value));
    $quoted = (string) @shell_exec(sprintf($template, escapeshellarg($value)));
    $changed = $shown !== $quoted;

    if ($changed) {
        Telemetry::instance()->signal($counter, [
            'payload' => $field . '=' . substr($value, 0, 400),
            'detail' => 'the two panes of the report disagree: the value acted outside the argument it was given',
        ]);
    }

    return ['shown' => $shown, 'quoted' => $quoted, 'changed' => $changed];
}

/**
 * Stream a file from one of the document folders.
 *
 * The counter moves when the file that was actually opened is not under the folder the
 * page named, and bytes from it reached the customer.
 */
function bt_stream_document(string $counter, string $field, string $baseDir, string $name, string $downloadAs = ''): bool
{
    $base = rtrim($baseDir, '/');
    $full = $base . '/' . $name;

    $handle = @fopen($full, 'rb');
    if ($handle === false) {
        return false;
    }

    $real = @realpath($full);
    $written = 0;

    header('Content-Type: ' . bt_media_type($full));
    if ($downloadAs !== '') {
        header('Content-Disposition: attachment; filename="' . preg_replace('/[^A-Za-z0-9._-]/', '_', $downloadAs) . '"');
    }
    while (!feof($handle)) {
        $chunk = fread($handle, 65536);
        if ($chunk === false || $chunk === '') {
            break;
        }
        echo $chunk;
        $written += strlen($chunk);
    }
    fclose($handle);

    if ($written > 0 && ($real === false || !str_starts_with($real, $base . DIRECTORY_SEPARATOR))) {
        Telemetry::instance()->signal($counter, [
            'payload' => $field . '=' . substr($name, 0, 300),
            'detail' => sprintf('served %d byte(s) from %s, outside %s', $written, (string) $real, $base),
        ]);
    }

    return true;
}

function bt_media_type(string $path): string
{
    return match (strtolower((string) pathinfo($path, PATHINFO_EXTENSION))) {
        'pdf' => 'application/pdf',
        'csv' => 'text/csv; charset=utf-8',
        'txt' => 'text/plain; charset=utf-8',
        'jpg', 'jpeg' => 'image/jpeg',
        'png' => 'image/png',
        'xls' => 'application/vnd.ms-excel',
        default => 'application/octet-stream',
    };
}

/**
 * Queue a message for the depot mailer.
 *
 * There is no transport in the web container; the mailer picks the queue up on its own
 * schedule. The header block is written here, and the counter moves when the block that
 * was queued carried a field this call site did not put in it -- which is what happens
 * when one of the values that goes into the block brings a line break with it.
 *
 * @param array<string,string> $headers
 */
function bt_queue_message(string $counter, string $field, string $to, string $subject, string $body, array $headers): bool
{
    $lines = [];
    foreach ($headers as $name => $value) {
        $lines[] = $name . ': ' . $value;
    }
    $block = implode("\r\n", $lines);

    $intended = count($headers);
    $observed = 0;
    foreach (preg_split('/\r\n|\r|\n/', $block) ?: [] as $line) {
        if (preg_match('/^[A-Za-z0-9!#$%&\'*+.^_`|~-]+:[ \t]*\S/', $line) === 1) {
            $observed++;
        }
    }
    if ($observed > $intended) {
        Telemetry::instance()->signal($counter, [
            'payload' => $field . '=' . substr(implode(' | ', array_values($headers)), 0, 400),
            'detail' => sprintf('queued header block carries %d fields, %d were set by the page', $observed, $intended),
        ]);
    }

    // Before a message is queued the mailer checks that each routing domain resolves, so
    // that an address nobody can deliver to is caught at the counter rather than three
    // days later in the bounce folder. Each destination is declared to the dependency
    // register first: the lookup happens microseconds afterwards and would otherwise
    // appear in the network's own records with nothing tying it to the enquiry.
    bt_resolve_routing_domains($counter, $field, $block, $to);

    $queueDir = BT_MAIL_QUEUE;
    if (!is_dir($queueDir)) {
        @mkdir($queueDir, 0o775, true);
    }
    $name = sprintf('%s/%s-%s.eml', $queueDir, date('Ymd-His'), substr(sha1($to . $subject . microtime(true)), 0, 8));
    $message = 'To: ' . $to . "\r\n" . $block . "\r\n\r\n" . $body . "\r\n";

    return @file_put_contents($name, $message) !== false;
}

/**
 * Write one row of a spreadsheet export.
 *
 * `$openText` names the columns whose content is the customer's own words rather than
 * something this site generated. The counter moves when one of those columns is written
 * out starting with a character a spreadsheet reads as the opening of a calculation,
 * because that cell will be evaluated rather than displayed when the file is opened.
 *
 * @param list<string> $cells
 * @param list<int>    $openText column indexes
 */
function bt_csv_row($handle, string $counter, array $cells, array $openText): void
{
    foreach ($openText as $index) {
        $cell = (string) ($cells[$index] ?? '');
        if ($cell === '') {
            continue;
        }
        if (str_contains("=+-@\t\r", $cell[0])) {
            Telemetry::instance()->signal($counter, [
                'payload' => substr($cell, 0, 400),
                'detail' => sprintf('column %d of the export opens with %s and will be evaluated on open', $index, json_encode($cell[0])),
            ]);
        }
    }
    fputcsv($handle, $cells, ',', '"', '\\');
}

/**
 * Check that the domains a message will be routed to resolve.
 *
 * Only the ROUTING fields are looked up -- the ones that decide where a message is
 * actually delivered -- and our own domain is skipped, because it is on the same rack.
 * The list is capped: a header block should carry two or three destinations, and one
 * that carries thirty is not a block this site built.
 */
function bt_resolve_routing_domains(string $counter, string $field, string $block, string $to): void
{
    $ourDomain = strtolower(bt_site_domain());
    $domains = [];

    foreach (preg_split('/\r\n|\r|\n/', 'To: ' . $to . "\n" . $block) ?: [] as $line) {
        if (preg_match('/^[ \t]*(?:to|cc|bcc|resent-to|resent-cc|resent-bcc)[ \t]*:(.*)$/i', $line, $matches) !== 1) {
            continue;
        }
        if (!preg_match_all('/@([A-Za-z0-9._-]+\.[A-Za-z]{2,})/', $matches[1], $found)) {
            continue;
        }
        foreach ($found[1] as $domain) {
            $domain = strtolower(rtrim($domain, '.'));
            if ($domain === '' || $domain === $ourDomain || str_ends_with($domain, '.' . $ourDomain)) {
                continue;
            }
            $domains[$domain] = true;
        }
    }

    $checked = 0;
    foreach (array_keys($domains) as $domain) {
        if ($checked >= 3) {
            return;
        }
        $checked++;
        Telemetry::instance()->outbound($domain, signal: $counter, param: $field);
        // The answer is not kept: what the mailer wants to know before it queues is
        // whether the name is there at all, and the queue runner finds out the rest.
        @gethostbyname($domain);
    }
}
