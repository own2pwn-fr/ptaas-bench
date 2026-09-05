<?php

declare(strict_types=1);

namespace Internal\Telemetry;

/**
 * Ordered, de-duplicated, bounded accumulator of described inputs.
 *
 * De-duplication is on (location, name, value hash) rather than (location, name).
 *
 * That choice is load-bearing. A name repeated with a DIFFERENT value is the
 * interesting case -- it is what makes two identical-looking requests behave
 * differently, because every layer in the stack picks a different one of them -- and
 * collapsing it to a single entry would hide exactly the requests this record exists
 * to explain. Identical repeats do collapse, because those carry nothing new.
 */
final class ParamBag
{
    /** @var list<array{name:string,in:string,value_sha256:string,value_len:int,sample:string}> */
    private array $entries = [];

    /** @var array<string,true> */
    private array $seen = [];

    public bool $truncated = false;

    public function __construct(
        private readonly int $max = 1024,
        private readonly int $sampleMax = Config::SAMPLE_MAX,
    ) {
    }

    public function add(string $name, string $location, string $value): void
    {
        if (count($this->entries) >= $this->max) {
            $this->truncated = true;

            return;
        }
        $entry = Params::describe($name, $location, $value, $this->sampleMax);
        $this->addEntry($entry);
    }

    /**
     * @param iterable<array{0:string,1:string}> $pairs
     */
    public function addMany(iterable $pairs, string $location): void
    {
        foreach ($pairs as $pair) {
            $this->add((string) $pair[0], $location, (string) $pair[1]);
        }
    }

    /**
     * Merge inputs that were already described elsewhere in the request.
     *
     * @param iterable<array<string,mixed>> $entries
     */
    public function extend(iterable $entries): void
    {
        foreach ($entries as $entry) {
            if (!isset($entry['name'], $entry['in'], $entry['value_sha256'])) {
                continue;
            }
            if (count($this->entries) >= $this->max) {
                $this->truncated = true;

                return;
            }
            /** @var array{name:string,in:string,value_sha256:string,value_len:int,sample:string} $entry */
            $this->addEntry($entry);
        }
    }

    /** @param array{name:string,in:string,value_sha256:string,value_len:int,sample:string} $entry */
    private function addEntry(array $entry): void
    {
        $key = $entry['in'] . "\0" . $entry['name'] . "\0" . $entry['value_sha256'];
        if (isset($this->seen[$key])) {
            return;
        }
        $this->seen[$key] = true;
        $this->entries[] = $entry;
    }

    /** @return list<array{name:string,in:string,value_sha256:string,value_len:int,sample:string}> */
    public function entries(): array
    {
        return $this->entries;
    }

    public function count(): int
    {
        return count($this->entries);
    }
}
