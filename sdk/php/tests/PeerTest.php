<?php

declare(strict_types=1);

namespace Internal\Telemetry\Tests;

use Internal\Telemetry\Config;
use Internal\Telemetry\Net;
use Internal\Telemetry\RequestContext;
use Internal\Telemetry\Telemetry;
use PHPUnit\Framework\TestCase;

/**
 * Which address classifies traffic, and which one is only ever description.
 *
 * The distinction is not pedantic. Records classified as generated are excluded from
 * the numbers this agent exists to produce, so an implementation that honoured a
 * forwarded header would let any caller decide whether its own traffic counted, simply
 * by naming one of our own addresses in a header it controls. Both this agent and the
 * collector had that bug once. Both now decide on the socket peer alone.
 */
final class PeerTest extends TestCase
{
    private string $spool = '';

    protected function setUp(): void
    {
        $this->spool = sys_get_temp_dir() . '/telemetry-peer-' . bin2hex(random_bytes(6)) . '.jsonl';
        Telemetry::reset();
        $_SERVER['REQUEST_METHOD'] = 'GET';
        $_SERVER['REQUEST_URI'] = '/';
    }

    protected function tearDown(): void
    {
        @unlink($this->spool);
        Telemetry::reset();
        unset($_SERVER['REMOTE_ADDR'], $_SERVER['HTTP_X_FORWARDED_FOR'], $_SERVER['HTTP_FORWARDED'], $_SERVER['REQUEST_METHOD'], $_SERVER['REQUEST_URI']);
    }

    private function agent(array $cidrs): Telemetry
    {
        return new Telemetry(new Config(
            service: 'orders',
            endpoint: 'http://127.0.0.1:1',
            spoolPath: $this->spool,
            timeout: 0.05,
            syntheticCidrs: $cidrs,
        ));
    }

    /** @return list<array<string,mixed>> */
    private function records(): array
    {
        $out = [];
        foreach (@file($this->spool, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) ?: [] as $line) {
            $decoded = json_decode($line, true);
            if (is_array($decoded)) {
                $out[] = $decoded;
            }
        }

        return $out;
    }

    public function testTheSocketPeerDecidesTheClassification(): void
    {
        $_SERVER['REMOTE_ADDR'] = '10.77.0.9';
        $agent = $this->agent(['10.77.0.0/24']);
        $agent->observe();
        $agent->signal('orders.filter.predicate_shift', ['detail' => 'x']);
        $agent->recordRequest(200);

        foreach ($this->records() as $record) {
            self::assertTrue($record['synthetic'], 'a peer inside the configured range is generated traffic');
            self::assertSame('10.77.0.9', $record['peer_ip']);
        }
    }

    public function testAForwardedHeaderCannotClassifyTraffic(): void
    {
        // The caller names one of our own addresses. If this decided anything, a caller
        // could erase its own traffic from every number we publish.
        $_SERVER['REMOTE_ADDR'] = '198.51.100.7';
        $_SERVER['HTTP_X_FORWARDED_FOR'] = '10.77.0.9';
        $agent = $this->agent(['10.77.0.0/24']);
        $agent->observe();
        $agent->signal('orders.filter.predicate_shift', ['detail' => 'x']);
        $agent->recordRequest(200);

        foreach ($this->records() as $record) {
            self::assertFalse($record['synthetic']);
            self::assertSame('198.51.100.7', $record['peer_ip']);
            self::assertSame('10.77.0.9', $record['client_ip'], 'the claim is kept as description');
        }
    }

    public function testAPeerThatIsItselfAClaimIsRefused(): void
    {
        // Something upstream has already overwritten the socket address with a header
        // value. The address is then the caller's claim, not the socket's, so it
        // classifies nothing and is not reported as a peer either.
        $_SERVER['REMOTE_ADDR'] = '10.77.0.9';
        $_SERVER['HTTP_X_FORWARDED_FOR'] = '10.77.0.9, 198.51.100.7';
        $agent = $this->agent(['10.77.0.0/24']);
        $agent->observe();
        $agent->recordRequest(200);

        $record = $this->records()[0];
        self::assertSame('', $record['peer_ip']);
        self::assertFalse($record['synthetic']);
    }

    public function testASignalRaisedDeepInAHandlerInheritsTheRequestClassification(): void
    {
        // There is no argument anywhere that would let this come out differently, which
        // is the point: an in-house self-check replaying its own request must never be
        // countable as organic traffic just because the signal was raised five frames
        // down.
        $_SERVER['REMOTE_ADDR'] = '10.77.0.4';
        $agent = $this->agent(['10.77.0.0/24']);
        $agent->observe();

        $deep = static function () use ($agent): void {
            $inner = static function () use ($agent): void {
                $agent->signal('library.document.read_scope', ['detail' => 'outside the document root']);
            };
            $inner();
        };
        $deep();

        $signals = array_values(array_filter($this->records(), static fn (array $r): bool => $r['type'] === 'signal'));
        self::assertCount(1, $signals);
        self::assertTrue($signals[0]['synthetic']);
        self::assertSame('10.77.0.4', $signals[0]['peer_ip']);
    }

    public function testNetworkMembership(): void
    {
        self::assertTrue(Net::inNetworks('10.77.0.1', ['10.77.0.0/24']));
        self::assertFalse(Net::inNetworks('10.78.0.1', ['10.77.0.0/24']));
        self::assertTrue(Net::inNetworks('10.77.0.130', ['10.77.0.128/25']));
        self::assertFalse(Net::inNetworks('10.77.0.126', ['10.77.0.128/25']));
        self::assertTrue(Net::inNetworks('fd00::5', ['fd00::/8']));
        self::assertFalse(Net::inNetworks('10.77.0.1', ['fd00::/8']));
        self::assertFalse(Net::inNetworks('not-an-address', ['10.77.0.0/24']));
        self::assertFalse(Net::inNetworks('10.77.0.1', ['nonsense/99']));
        self::assertFalse(Net::inNetworks('10.77.0.1', []));
    }

    public function testForwardedClaimDetection(): void
    {
        self::assertTrue(Net::peerMatchesForwardedClaim('10.77.0.9', ['x-forwarded-for' => '10.77.0.9']));
        self::assertTrue(Net::peerMatchesForwardedClaim('10.77.0.9', ['forwarded' => 'for=10.77.0.9;proto=http']));
        self::assertTrue(Net::peerMatchesForwardedClaim('10.77.0.9', ['x-real-ip' => '10.77.0.9:4433']));
        self::assertFalse(Net::peerMatchesForwardedClaim('10.77.0.9', ['x-forwarded-for' => '198.51.100.7']));
        self::assertFalse(Net::peerMatchesForwardedClaim('', ['x-forwarded-for' => '198.51.100.7']));
    }

    public function testContextIsDerivedOnceAndOnlyFromTheEnvironment(): void
    {
        $_SERVER['REMOTE_ADDR'] = '10.77.0.4';
        $context = RequestContext::fromServer($_SERVER, ['10.77.0.0/24'], 1024);
        self::assertTrue($context->synthetic);
        self::assertSame('10.77.0.4', $context->peerIp);

        $agent = $this->agent(['10.77.0.0/24']);
        $first = $agent->context();
        self::assertSame($first, $agent->context(), 'the context is derived once per process');
    }
}
