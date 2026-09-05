<?php

declare(strict_types=1);

namespace Internal\Telemetry\Tests;

use Internal\Telemetry\Config;
use Internal\Telemetry\Params;
use Internal\Telemetry\ParamBag;
use Internal\Telemetry\RequestContext;
use Internal\Telemetry\Telemetry;
use PHPUnit\Framework\TestCase;

/**
 * The shape of what goes on the wire, and the input enumeration behind it.
 */
final class RecordShapeTest extends TestCase
{
    private string $spool = '';

    protected function setUp(): void
    {
        $this->spool = sys_get_temp_dir() . '/telemetry-test-' . bin2hex(random_bytes(6)) . '.jsonl';
        Telemetry::reset();
    }

    protected function tearDown(): void
    {
        @unlink($this->spool);
        Telemetry::reset();
        $_SERVER = array_diff_key($_SERVER, array_flip(['REQUEST_METHOD', 'REQUEST_URI', 'QUERY_STRING', 'REMOTE_ADDR', 'HTTP_COOKIE', 'CONTENT_TYPE', 'HTTP_USER_AGENT', 'HTTP_X_FORWARDED_FOR']));
        $_POST = [];
        $_FILES = [];
    }

    private function agent(array $overrides = []): Telemetry
    {
        return new Telemetry(new Config(
            service: 'orders',
            endpoint: $overrides['endpoint'] ?? 'http://127.0.0.1:1',
            spoolPath: $this->spool,
            timeout: 0.05,
            syntheticCidrs: $overrides['syntheticCidrs'] ?? [],
        ));
    }

    /** @return list<array<string,mixed>> */
    private function records(): array
    {
        if (!is_file($this->spool)) {
            return [];
        }
        $out = [];
        foreach (file($this->spool, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) ?: [] as $line) {
            $decoded = json_decode($line, true);
            if (is_array($decoded)) {
                $out[] = $decoded;
            }
        }

        return $out;
    }

    public function testExactlyOneRequestRecordPerRequest(): void
    {
        $_SERVER['REQUEST_METHOD'] = 'GET';
        $_SERVER['REQUEST_URI'] = '/orders/4192?view=full';
        $_SERVER['QUERY_STRING'] = 'view=full';
        $_SERVER['REMOTE_ADDR'] = '198.51.100.7';

        $agent = $this->agent();
        $agent->observe();
        $agent->route('/orders/{id}', ['id' => '4192']);
        $agent->recordRequest(200);
        $agent->recordRequest(200);
        $agent->recordRequest(200);

        $requests = array_values(array_filter($this->records(), static fn (array $r): bool => $r['type'] === 'http_request'));
        self::assertCount(1, $requests);
        self::assertSame('/orders/{id}', $requests[0]['route']);
        self::assertSame('/orders/4192', $requests[0]['path']);
        self::assertSame(200, $requests[0]['status']);
    }

    public function testUnmatchedRouteIsReportedAsSuch(): void
    {
        $_SERVER['REQUEST_METHOD'] = 'GET';
        $_SERVER['REQUEST_URI'] = '/nothing/here';
        $_SERVER['REMOTE_ADDR'] = '198.51.100.7';

        $agent = $this->agent();
        $agent->observe();
        $agent->recordRequest(404);

        self::assertSame(RequestContext::UNMATCHED, $this->records()[0]['route']);
    }

    public function testRepeatedNameWithDifferentValuesSurvives(): void
    {
        // The whole reason de-duplication is on the value hash. Collapsing these would
        // erase the technique that produced them.
        $_SERVER['REQUEST_METHOD'] = 'GET';
        $_SERVER['REQUEST_URI'] = '/search?q=chairs&q=1%27+OR+%271%27%3D%271';
        $_SERVER['QUERY_STRING'] = 'q=chairs&q=1%27+OR+%271%27%3D%271';
        $_SERVER['REMOTE_ADDR'] = '198.51.100.7';

        $agent = $this->agent();
        $agent->observe();
        $agent->recordRequest(200);

        $params = $this->records()[0]['params'];
        $q = array_values(array_filter($params, static fn (array $p): bool => $p['name'] === 'q' && $p['in'] === 'query'));
        self::assertCount(2, $q);
        self::assertNotSame($q[0]['value_sha256'], $q[1]['value_sha256']);
    }

    public function testIdenticalRepeatsCollapse(): void
    {
        $bag = new ParamBag();
        $bag->add('q', 'query', 'chairs');
        $bag->add('q', 'query', 'chairs');
        self::assertSame(1, $bag->count());
    }

    public function testEveryLocationIsEnumerated(): void
    {
        $_SERVER['REQUEST_METHOD'] = 'POST';
        $_SERVER['REQUEST_URI'] = '/quotes?ref=Q-77';
        $_SERVER['QUERY_STRING'] = 'ref=Q-77';
        $_SERVER['REMOTE_ADDR'] = '198.51.100.7';
        $_SERVER['HTTP_COOKIE'] = 'sid=abc; view=grid';
        $_SERVER['HTTP_USER_AGENT'] = 'curl/8';
        $_SERVER['HTTP_X_SITE'] = 'north';

        $agent = $this->agent();
        $agent->observe();
        $agent->route('/quotes', []);
        $agent->addParams([['company', 'Marsh & Co']], 'body');
        $agent->recordRequest(200);

        $params = $this->records()[0]['params'];
        $locations = array_unique(array_column($params, 'in'));
        sort($locations);
        self::assertSame(['body', 'cookie', 'header', 'query'], $locations);

        $byName = array_column($params, 'sample', 'name');
        self::assertSame('Q-77', $byName['ref']);
        self::assertSame('abc', $byName['sid']);
        self::assertSame('north', $byName['x-site']);
        self::assertSame('Marsh & Co', $byName['company']);
    }

    public function testJsonBodyIsFlattenedByDottedPath(): void
    {
        $flat = Params::flattenJson(json_decode('{"filter":{"tags":["a","b"]},"page":2,"live":true}', true));
        $map = [];
        foreach ($flat as [$name, $value]) {
            $map[$name] = $value;
        }
        self::assertSame('a', $map['filter.tags.0']);
        self::assertSame('b', $map['filter.tags.1']);
        self::assertSame('2', $map['page']);
        self::assertSame('true', $map['live']);
    }

    public function testValueHashMatchesTheRawBytes(): void
    {
        $entry = Params::describe('q', 'query', 'chairs');
        self::assertSame(hash('sha256', 'chairs'), $entry['value_sha256']);
        self::assertSame(6, $entry['value_len']);
    }

    public function testSampleIsCutOnACharacterBoundary(): void
    {
        $value = str_repeat('e', 255) . 'é';
        $clipped = Params::clip($value, Config::SAMPLE_MAX);
        self::assertSame(255, strlen($clipped));
        self::assertTrue(json_encode($clipped) !== false);
    }

    public function testSignalCarriesItsAttributesAndTheRequestId(): void
    {
        $_SERVER['REQUEST_METHOD'] = 'GET';
        $_SERVER['REQUEST_URI'] = '/help';
        $_SERVER['REMOTE_ADDR'] = '198.51.100.7';

        $agent = $this->agent();
        $agent->observe();
        $agent->signal('help.article.include_scope', ['payload' => '../../etc/passwd', 'detail' => 'resolved outside the article directory']);

        $signals = array_values(array_filter($this->records(), static fn (array $r): bool => $r['type'] === 'signal'));
        self::assertCount(1, $signals);
        self::assertSame('help.article.include_scope', $signals[0]['signal']);
        self::assertSame('resolved outside the article directory', $signals[0]['attributes']['detail']);
        self::assertSame($agent->requestId(), $signals[0]['attributes']['request_id']);
    }

    public function testAMalformedSignalNameIsCountedAndDropped(): void
    {
        $agent = $this->agent();
        $agent->signal('NotAMetricName');
        $agent->signal('two.segments');
        self::assertSame([], $this->records());
        self::assertSame(2, $agent->stats()['invalid_names']);
    }

    public function testNoRecordCarriesACatalogueIdentifier(): void
    {
        // A record identifies a sink by an opaque metric name and by nothing else.
        $_SERVER['REQUEST_METHOD'] = 'GET';
        $_SERVER['REQUEST_URI'] = '/';
        $_SERVER['REMOTE_ADDR'] = '198.51.100.7';
        $agent = $this->agent();
        $agent->observe();
        $agent->signal('layout.skin.include_scope', ['detail' => 'x']);
        $agent->recordRequest(200);
        foreach ($this->records() as $record) {
            self::assertArrayNotHasKey('vuln_id', $record);
            self::assertContains($record['type'], ['http_request', 'signal', 'note', 'correlation']);
        }
    }
}
