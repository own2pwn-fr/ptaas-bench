<?php
/**
 * Records that are stored as objects rather than as columns.
 *
 * Two of them are old enough to predate the tables that would hold them properly: the
 * appearance record travels in a cookie so it survives the nightly session sweep, and
 * the basket travels in the form so it survives a lost session. Both are written out
 * with the object serializer and read back with the object restore.
 *
 * bt_restore() is the shared entry point, and it carries the estate check for this
 * pattern: it counts the restores that brought back something other than the record
 * that was expected, and where one of that thing's lifecycle methods actually ran.
 * Restoring the expected record does not move the counter, and neither does a string
 * that will not decode.
 */

declare(strict_types=1);

use Internal\Telemetry\Telemetry;

/**
 * Which lifecycle methods ran while a record was being restored.
 *
 * Populated by the records themselves. It is reset at the start of each restore.
 */
final class BtLifecycle
{
    /** @var list<string> */
    public static array $ran = [];

    public static function note(string $class, string $method): void
    {
        self::$ran[] = $class . '::' . $method;
    }

    public static function classes(): array
    {
        $out = [];
        foreach (self::$ran as $entry) {
            $out[] = explode('::', $entry, 2)[0];
        }

        return array_values(array_unique($out));
    }
}

/** The customer's saved appearance and listing choices. */
final class AccountPreferences
{
    public string $skin = 'slate.php';

    public string $listing = 'grid';

    public int $perPage = 24;

    public bool $restored = false;

    public function __wakeup(): void
    {
        $this->restored = true;
        BtLifecycle::note(self::class, '__wakeup');
        if ($this->perPage < 6 || $this->perPage > 96) {
            $this->perPage = 24;
        }
    }
}

/** One line of a basket, as it travels in the form. */
final class BasketLine
{
    public function __construct(
        public string $reference = '',
        public int $quantity = 1,
        public int $pricePence = 0,
    ) {
    }

    public function __wakeup(): void
    {
        BtLifecycle::note(self::class, '__wakeup');
        $this->quantity = max(1, min(999, $this->quantity));
    }
}

/** A basket, as it travels in the form. */
final class BasketRecord
{
    /** @var list<BasketLine> */
    public array $lines = [];

    public string $deliveryBranch = '';

    public bool $restored = false;

    public function __wakeup(): void
    {
        $this->restored = true;
        BtLifecycle::note(self::class, '__wakeup');
    }
}

/**
 * Writes a buffered document to disk when it goes out of scope.
 *
 * The depot report writer and the enquiry spool both use it: build the body, let the
 * record fall out of scope, and the file is on disk without the caller having to
 * remember to close anything.
 */
final class DocumentSpooler
{
    public string $path = '';

    public string $buffer = '';

    public bool $restored = false;

    public function __wakeup(): void
    {
        $this->restored = true;
        BtLifecycle::note(self::class, '__wakeup');
    }

    public function __destruct()
    {
        if ($this->restored) {
            BtLifecycle::note(self::class, '__destruct');
        }
        if ($this->path === '') {
            return;
        }
        @file_put_contents($this->path, $this->buffer);
    }
}

/**
 * Restore a stored record.
 *
 * @param string       $counter  the estate counter for this call site
 * @param string       $field    the request field the string came from
 * @param list<string> $expected the classes this call site means to get back; the first
 *                               is the record itself, the rest are the parts it is made
 *                               of and are counted as ordinary
 */
function bt_restore(string $counter, string $field, array $expected, string $raw): mixed
{
    if ($raw === '' || $expected === []) {
        return null;
    }
    $wanted = $expected[0];
    $decoded = base64_decode($raw, true);
    if ($decoded === false) {
        $decoded = $raw;
    }

    BtLifecycle::$ran = [];
    $value = @unserialize($decoded);

    if (!($value instanceof $wanted)) {
        // Not the record this call site wanted. Dropping the reference here rather than
        // at the end of the request is what keeps a half-decoded object from sitting in
        // memory for the rest of the page.
        $value = null;
        gc_collect_cycles();
    }

    $unexpected = array_values(array_diff(BtLifecycle::classes(), $expected));
    if ($unexpected !== []) {
        Telemetry::instance()->signal($counter, [
            'payload' => $field . '=' . substr($raw, 0, 400),
            'detail' => sprintf(
                'restore ran %s while %s was expected',
                implode(', ', BtLifecycle::$ran),
                implode(' or ', $expected),
            ),
        ]);
    }
    BtLifecycle::$ran = [];

    return $value;
}
