<?php

declare(strict_types=1);

namespace Internal\Telemetry\Tests;

use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\TestCase;
use Internal\Telemetry\Telemetry;
use Internal\Telemetry\Config;

/**
 * Sweep everything that ships for words that give the game away.
 *
 * Services that install this package handle untrusted input for a living, so it is
 * prudent to assume that at some point someone reads the source, the vendor directory,
 * the environment and the spool of a running container. What they find has to read as
 * an ordinary observability agent, because that is what it is: metric-shaped signal
 * names, TELEMETRY_* configuration, request records. Vocabulary from the assurance
 * programme that commissioned it belongs in the programme's own repository, not in a
 * library installed next to a payments handler.
 *
 * Matching is TOKEN-based, never substring-based. "available", "collaborator",
 * "elaborate" and "labour" all contain "lab"; "flagship" contains "flag"; "latest" and
 * "contest" contain "test". A sweep that shouted about those would be switched off
 * inside a week, and a sweep that is switched off protects nothing. Text is split on
 * punctuation and on case boundaries, then compared against whole words, against
 * consecutive-token phrases, and against a short list of stems so that one entry covers
 * a word and its inflections.
 *
 * The test tree is excluded on purpose: it does not ship, and it is the one place the
 * vocabulary below is allowed to live.
 */
final class NoTellsTest extends TestCase
{
    /**
     * Matched against the START of a token, so plurals and inflections are covered.
     */
    private const STEMS = [
        'bench', 'vuln', 'exploit', 'trigger', 'oracle', 'canary', 'honeypot', 'testbed',
        'sandbox', 'scanner', 'scoring', 'scorer', 'evaluation', 'challenge', 'insecure',
        'deliberate', 'attacker', 'adversar', 'malicious', 'planted', 'grader', 'pentest',
        'dvwa', 'pwn',
    ];

    /**
     * Whole tokens only. Each of these is a prefix of an ordinary word, so a stem match
     * would cry wolf: label, labour, collaborator, flagship, contest.
     */
    private const WORDS = ['lab', 'labs', 'ctf', 'cve', 'poc', 'flag', 'flags', 'flagged', 'flagging'];

    /** Consecutive tokens, so "ground truth" is caught while "ground" alone is not. */
    private const PHRASES = [
        ['ground', 'truth'],
        ['under', 'test'],
        ['penetration', 'test'],
        ['juice', 'shop'],
        ['red', 'team'],
        ['capture', 'the', 'flag'],
    ];

    /** @return list<string> */
    private static function tokenise(string $text): array
    {
        // Split camelCase and PascalCase first, so BenchClient, bench_client and
        // bench-client all yield "bench".
        $spaced = preg_replace('/(?<=[a-z0-9])(?=[A-Z])/', ' ', $text) ?? $text;

        return array_values(array_filter(preg_split('/[^A-Za-z0-9]+/', strtolower($spaced)) ?: []));
    }

    /** @return list<string> */
    public static function hits(string $text): array
    {
        $tokens = self::tokenise($text);
        $found = [];
        foreach ($tokens as $token) {
            if (in_array($token, self::WORDS, true)) {
                $found[] = $token;
                continue;
            }
            foreach (self::STEMS as $stem) {
                if (str_starts_with($token, $stem)) {
                    $found[] = $stem;
                    break;
                }
            }
        }
        foreach (self::PHRASES as $phrase) {
            $width = count($phrase);
            for ($i = 0; $i + $width <= count($tokens); $i++) {
                if (array_slice($tokens, $i, $width) === $phrase) {
                    $found[] = implode(' ', $phrase);
                    break;
                }
            }
        }
        $found = array_values(array_unique($found));
        sort($found);

        return $found;
    }

    /** @return list<string> absolute paths of everything that ships */
    private static function shippedFiles(): array
    {
        $root = dirname(__DIR__);
        $files = [$root . '/composer.json'];
        foreach (['src', 'bin', 'examples'] as $directory) {
            $path = $root . '/' . $directory;
            if (!is_dir($path)) {
                continue;
            }
            $iterator = new \RecursiveIteratorIterator(new \RecursiveDirectoryIterator($path, \FilesystemIterator::SKIP_DOTS));
            foreach ($iterator as $entry) {
                if ($entry->isFile()) {
                    $files[] = $entry->getPathname();
                }
            }
        }
        sort($files);

        return $files;
    }

    /** @return list<array{0:string}> */
    public static function shippedFileProvider(): array
    {
        return array_map(static fn (string $path): array => [$path], self::shippedFiles());
    }

    public function testTheSweepCatchesWhatItIsFor(): void
    {
        // A guard rail nobody has watched fail is a guard rail nobody can trust.
        $leaks = [
            'this is a benchmark target',
            'lists the vulnerabilities',
            'the sink triggered once',
            'we captured the flags',
            'stored as ground truth',
            'class BenchClient',
            'BENCH_COLLECTOR_URL',
            'a lab environment',
            'raised by the oracle',
            'what the scanner reported',
            'the tool under test',
        ];
        foreach ($leaks as $leak) {
            self::assertNotSame([], self::hits($leak), $leak . ' should have been caught');
        }
        self::assertContains('ground truth', self::hits('stored as ground truth'));
        self::assertContains('bench', self::hits('class BenchClient'));
    }

    public function testTheSweepDoesNotCryWolf(): void
    {
        // The reason this is token-based: every one of these contains a forbidden
        // substring.
        foreach (['available', 'collaborator', 'elaborate', 'labelled', 'labour', 'flagship',
                  'contest', 'latest', 'protest', 'spawned', 'score', 'scoreboard'] as $innocent) {
            self::assertSame([], self::hits($innocent), $innocent . ' must not be a hit');
        }
        self::assertSame([], self::hits('an ordinary telemetry agent exporting request records'));
    }

    #[DataProvider('shippedFileProvider')]
    public function testShippedFilesCarryNoTell(string $path): void
    {
        $text = @file_get_contents($path);
        if ($text === false) {
            self::markTestSkipped($path . ' is unreadable');
        }
        self::assertSame([], self::hits($text), $path . ' leaks ' . implode(', ', self::hits($text)));
    }

    public function testShippedFileNamesCarryNoTell(): void
    {
        foreach (self::shippedFiles() as $path) {
            self::assertSame([], self::hits(basename($path)), basename($path) . ' is itself a tell');
        }
    }

    public function testEveryEnvironmentVariableIsTelemetryShaped(): void
    {
        // A stray BENCH_* lookup would show up in `env` on a compromised host.
        $names = [];
        foreach (self::shippedFiles() as $path) {
            if (!str_ends_with($path, '.php') && basename($path) !== 'telemetry-drain') {
                continue;
            }
            $source = (string) @file_get_contents($path);
            if (preg_match_all('/getenv\(\s*[\'"]([A-Z0-9_]+)[\'"]/', $source, $matches)) {
                foreach ($matches[1] as $name) {
                    $names[$name] = true;
                }
            }
            if (preg_match_all('/\$_SERVER\[\s*[\'"]([A-Z0-9_]+)[\'"]\s*\]/', $source, $matches)) {
                foreach ($matches[1] as $name) {
                    // CGI-standard keys are the SAPI's, not ours.
                    if (!str_starts_with($name, 'HTTP_') && !in_array($name, [
                        'REQUEST_METHOD', 'REQUEST_URI', 'QUERY_STRING', 'REMOTE_ADDR',
                        'CONTENT_TYPE', 'CONTENT_LENGTH', 'SCRIPT_FILENAME', 'SCRIPT_NAME',
                        'SERVER_NAME', 'SERVER_PORT', 'DOCUMENT_ROOT', 'HTTPS', 'PATH_INFO',
                    ], true)) {
                        $names[$name] = true;
                    }
                }
            }
        }
        self::assertNotSame([], $names, 'expected the agent to read its configuration from the environment');
        foreach (array_keys($names) as $name) {
            self::assertStringStartsWith('TELEMETRY_', $name, $name . ' is not part of this agent');
        }
    }

    public function testNoPublicSymbolCarriesATell(): void
    {
        foreach (['Config', 'HttpPoster', 'Net', 'ParamBag', 'Params', 'RequestContext', 'Spool', 'Telemetry'] as $class) {
            self::assertSame([], self::hits($class));
            $reflection = new \ReflectionClass('Internal\\Telemetry\\' . $class);
            foreach ($reflection->getMethods(\ReflectionMethod::IS_PUBLIC) as $method) {
                self::assertSame([], self::hits($method->getName()), $class . '::' . $method->getName() . ' is a tell');
            }
            foreach ($reflection->getConstants() as $name => $_) {
                self::assertSame([], self::hits((string) $name), $class . '::' . $name . ' is a tell');
            }
        }
    }

    public function testTheWireShapeCarriesNoTell(): void
    {
        $spool = sys_get_temp_dir() . '/telemetry-tells-' . bin2hex(random_bytes(6)) . '.jsonl';
        $_SERVER['REQUEST_METHOD'] = 'GET';
        $_SERVER['REQUEST_URI'] = '/';
        $_SERVER['REMOTE_ADDR'] = '198.51.100.7';
        try {
            $agent = new Telemetry(new Config(service: 'orders', endpoint: 'http://127.0.0.1:1', spoolPath: $spool, timeout: 0.05));
            $agent->observe();
            $agent->signal('catalogue.reference.predicate_shift', ['payload' => 'x', 'detail' => 'y']);
            $agent->note('hello');
            $agent->outbound('http://f00d.example/x', signal: 'imports.feed.fetch_external');
            $agent->recordRequest(200);

            $lines = file($spool, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) ?: [];
            self::assertNotEmpty($lines);
            foreach ($lines as $line) {
                $record = json_decode($line, true);
                self::assertIsArray($record);
                foreach (array_keys($record) as $key) {
                    self::assertSame([], self::hits((string) $key), 'field ' . $key . ' is a tell');
                }
                self::assertContains($record['type'] ?? 'correlation', ['signal', 'note', 'http_request', 'correlation']);
            }
        } finally {
            @unlink($spool);
            Telemetry::reset();
            unset($_SERVER['REQUEST_METHOD'], $_SERVER['REQUEST_URI'], $_SERVER['REMOTE_ADDR']);
        }
    }
}
