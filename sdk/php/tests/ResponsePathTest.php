<?php

declare(strict_types=1);

namespace Internal\Telemetry\Tests;

use Internal\Telemetry\Config;
use Internal\Telemetry\Telemetry;
use PHPUnit\Framework\TestCase;

/**
 * The agent must be invisible in a response, in what it costs and in what it emits.
 *
 * The cost assertions below are written against an endpoint that CANNOT answer:
 * 192.0.2.1 is reserved for documentation and is not routable, so a connection to it
 * either hangs until the configured timeout or fails after the operating system has
 * spent real time on it. The configured timeout is deliberately far larger than the
 * budget the assertions allow. If any of the recording calls touched the network, the
 * arithmetic would not work out and these tests would fail by seconds, not by
 * milliseconds -- which is the only way to state "does not talk to the collector" as a
 * fact rather than as a comment.
 */
final class ResponsePathTest extends TestCase
{
    private const UNREACHABLE = 'http://192.0.2.1:8900';

    private string $spool = '';

    protected function setUp(): void
    {
        $this->spool = sys_get_temp_dir() . '/telemetry-cost-' . bin2hex(random_bytes(6)) . '.jsonl';
        Telemetry::reset();
        $_SERVER['REQUEST_METHOD'] = 'GET';
        $_SERVER['REQUEST_URI'] = '/catalogue?section=fixings';
        $_SERVER['QUERY_STRING'] = 'section=fixings';
        $_SERVER['REMOTE_ADDR'] = '198.51.100.7';
    }

    protected function tearDown(): void
    {
        @unlink($this->spool);
        Telemetry::reset();
        unset($_SERVER['REQUEST_METHOD'], $_SERVER['REQUEST_URI'], $_SERVER['QUERY_STRING'], $_SERVER['REMOTE_ADDR']);
    }

    private function agent(string $endpoint): Telemetry
    {
        return new Telemetry(new Config(
            service: 'orders',
            endpoint: $endpoint,
            spoolPath: $this->spool,
            // Thirty seconds. Nothing on the recording path may consult this.
            timeout: 30.0,
        ));
    }

    public function testADeadCollectorCostsTheRequestNothing(): void
    {
        $agent = $this->agent(self::UNREACHABLE);

        $started = microtime(true);
        $agent->observe();
        $agent->route('/catalogue', []);
        for ($i = 0; $i < 200; $i++) {
            $agent->signal('catalogue.section.predicate_shift', [
                'payload' => str_repeat('x', 400),
                'detail' => 'row set differs from the parameterised control',
            ]);
        }
        $agent->recordRequest(200);
        $elapsed = microtime(true) - $started;

        self::assertLessThan(
            1.0,
            $elapsed,
            'a request that raised 200 signals against an unreachable collector took ' . round($elapsed, 3) . 's',
        );
        self::assertSame(201, count(file($this->spool, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) ?: []));
    }

    public function testTheSameWorkCostsTheSameWhicheverEndpointIsConfigured(): void
    {
        // If the endpoint were consulted at all, these two would not agree.
        $measure = function (string $endpoint): float {
            $agent = $this->agent($endpoint);
            $started = microtime(true);
            $agent->observe();
            for ($i = 0; $i < 100; $i++) {
                $agent->signal('catalogue.section.predicate_shift', ['detail' => 'x']);
            }
            $agent->recordRequest(200);

            return microtime(true) - $started;
        };

        $unreachable = $measure(self::UNREACHABLE);
        @unlink($this->spool);
        Telemetry::reset();
        $refused = $measure('http://127.0.0.1:1');

        self::assertLessThan(0.5, abs($unreachable - $refused));
    }

    public function testTheOneCallThatDoesUseTheNetworkIsBounded(): void
    {
        // outbound() is the single exception, and it is capped: it describes a name
        // lookup that is about to happen, so it cannot wait for the next drain, but it
        // also cannot be allowed to hold a response open.
        $agent = new Telemetry(new Config(
            service: 'orders',
            endpoint: self::UNREACHABLE,
            spoolPath: $this->spool,
            timeout: 0.2,
        ));
        $agent->observe();

        $started = microtime(true);
        $agent->outbound('http://supplier-feed.invalid/catalogue.xml', signal: 'imports.feed.fetch_external', param: 'source');
        $elapsed = microtime(true) - $started;

        self::assertLessThan(2.0, $elapsed, 'outbound() took ' . round($elapsed, 3) . 's against an unreachable collector');
        // The immediate path failed, so the record went the ordinary way instead of
        // being lost.
        $lines = file($this->spool, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) ?: [];
        self::assertNotEmpty($lines);
        $record = json_decode($lines[0], true);
        self::assertSame('correlation', $record['type']);
        self::assertSame('supplier-feed.invalid', $record['destination_host']);
        self::assertSame('imports.feed.fetch_external', $record['signal']);
    }

    public function testNothingIsAddedToTheResponse(): void
    {
        $before = headers_list();
        $agent = $this->agent(self::UNREACHABLE);
        $agent->observe();
        $agent->route('/catalogue', []);
        $agent->signal('catalogue.section.predicate_shift', ['detail' => 'x']);
        $agent->recordRequest(200);

        self::assertSame($before, headers_list(), 'the agent must not put anything in the response header block');
    }

    public function testARecordingCallNeverThrows(): void
    {
        // A spool path that cannot be created, which is as broken as the write side gets.
        $agent = new Telemetry(new Config(
            service: 'orders',
            endpoint: self::UNREACHABLE,
            spoolPath: "/proc/telemetry-cannot-exist/records.jsonl",
            timeout: 0.05,
        ));
        $agent->observe();
        $agent->route('/catalogue', []);
        $agent->signal('catalogue.section.predicate_shift', ['detail' => 'x']);
        $agent->note('nothing to see');
        $agent->recordRequest(200);

        self::assertGreaterThan(0, $agent->stats()['dropped']);
    }
}
