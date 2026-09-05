using System;
using System.Collections.Generic;
using System.Net;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.AspNetCore.Http;
using Xunit;

namespace Internal.Telemetry.Tests;

/// <summary>
/// The classification of traffic as the estate's own is decided on the connection
/// address and on nothing else. If a caller could steer it, a caller could remove its
/// own traffic from every dashboard it appears in.
/// </summary>
public sealed class PeerTests
{
    private const string ProbeRange = "10.77.0.0/24";

    private static TelemetryClient Client(FakeCollector collector)
    {
        return new TelemetryClient(new TelemetryOptions
        {
            Service = "portal",
            Endpoint = "http://collector.internal:8900",
            SyntheticCidrs = new[] { ProbeRange },
            MessageHandler = collector,
            EnvironmentReader = _ => null,
        });
    }

    private static DefaultHttpContext Request(string peer, params (string Name, string Value)[] headers)
    {
        DefaultHttpContext context = new();
        context.Request.Method = "GET";
        context.Request.Path = "/help/directory";
        context.Connection.RemoteIpAddress = IPAddress.Parse(peer);
        foreach ((string name, string value) in headers)
        {
            context.Request.Headers[name] = value;
        }

        return context;
    }

    private static async Task<JsonElement> RecordOne(TelemetryClient client, FakeCollector collector, HttpContext context)
    {
        TelemetryMiddleware middleware = new(_ => Task.CompletedTask, client, new TelemetryMiddlewareOptions());
        await middleware.InvokeAsync(context);
        await client.FlushAsync(TimeSpan.FromSeconds(2));
        List<JsonElement> records = collector.Records("http_request");
        Assert.Single(records);
        return records[0];
    }

    [Fact]
    public async Task ConnectionAddressInsideTheRangeIsClassifiedAsTheEstatesOwn()
    {
        FakeCollector collector = new();
        await using TelemetryClient client = Client(collector);
        JsonElement record = await RecordOne(client, collector, Request("10.77.0.5"));

        Assert.Equal("10.77.0.5", record.GetProperty("peer_ip").GetString());
        Assert.True(record.GetProperty("synthetic").GetBoolean());
    }

    [Fact]
    public async Task AForwardedHeaderCannotClaimTheClassification()
    {
        // The bug this asserts against: a caller announcing an address inside the
        // probe range and thereby erasing its own traffic from the record.
        FakeCollector collector = new();
        await using TelemetryClient client = Client(collector);
        JsonElement record = await RecordOne(
            client,
            collector,
            Request("10.88.0.31", ("X-Forwarded-For", "10.77.0.5")));

        Assert.Equal("10.88.0.31", record.GetProperty("peer_ip").GetString());
        Assert.False(record.TryGetProperty("synthetic", out JsonElement marker) && marker.GetBoolean());

        // The claim is still described, because it is evidence about the request.
        Assert.Equal("10.77.0.5", record.GetProperty("client_ip").GetString());
    }

    [Fact]
    public async Task AnAddressAlreadyRewrittenFromAHeaderIsNotTreatedAsAPeer()
    {
        // What the framework's forwarded-headers component leaves behind: the connection
        // address has been replaced by the header value, the consumed entry has been
        // removed from X-Forwarded-For, and the original is filed under X-Original-For.
        // Reading the connection at that point reads the caller's claim.
        FakeCollector collector = new();
        await using TelemetryClient client = Client(collector);
        DefaultHttpContext context = Request(
            "10.77.0.5",
            ("X-Original-For", "10.88.0.31:52344"));

        JsonElement record = await RecordOne(client, collector, context);

        Assert.False(record.TryGetProperty("peer_ip", out JsonElement peer) && peer.ValueKind != JsonValueKind.Null);
        Assert.False(record.TryGetProperty("synthetic", out JsonElement marker) && marker.GetBoolean());
    }

    [Fact]
    public void AnAddressThatTheCallerAlsoAnnouncedIsRecognisedAsAClaim()
    {
        DefaultHttpContext context = Request("10.77.0.5", ("X-Forwarded-For", "10.77.0.5, 203.0.113.9"));
        Assert.True(PeerResolution.WasRewritten(context.Request, IPAddress.Parse("10.77.0.5")));
        Assert.Null(PeerResolution.Peer(context));
    }

    [Fact]
    public void ForwardedSyntaxIsUnderstood()
    {
        DefaultHttpContext context = Request("198.51.100.4", ("Forwarded", "for=198.51.100.4;proto=https"));
        Assert.True(PeerResolution.WasRewritten(context.Request, IPAddress.Parse("198.51.100.4")));
    }

    [Fact]
    public void MappedAddressesFoldBackToIpv4BeforeBeingMatched()
    {
        SourceMatcher matcher = SourceMatcher.Compile(new[] { ProbeRange });
        Assert.True(matcher.Matches("::ffff:10.77.0.9"));
        Assert.True(matcher.Matches("10.77.0.9:41022"));
        Assert.False(matcher.Matches("10.88.0.9"));
        Assert.False(matcher.Matches("not an address"));
        Assert.False(matcher.Matches((string?)null));
    }

    [Fact]
    public void PrefixArithmeticIsExactOnByteBoundariesAndInsideThem()
    {
        SourceMatcher matcher = SourceMatcher.Compile(new[] { "10.77.0.0/25", "2001:db8::/32", "bad/entry", "10.0.0.1" });
        Assert.True(matcher.Matches("10.77.0.127"));
        Assert.False(matcher.Matches("10.77.0.128"));
        Assert.True(matcher.Matches("2001:db8:1::9"));
        Assert.False(matcher.Matches("2001:db9::9"));
        Assert.True(matcher.Matches("10.0.0.1"));
        Assert.Equal(3, matcher.Count);
    }

    [Fact]
    public async Task ARaisedCounterCarriesTheSameAddressAsTheRequestRecord()
    {
        FakeCollector collector = new();
        await using TelemetryClient client = Client(collector);
        DefaultHttpContext context = Request("10.77.0.5");

        TelemetryMiddleware middleware = new(
            _ =>
            {
                client.Signal("portal.documents.write.path_escape", detail: "raised inside the handler");
                return Task.CompletedTask;
            },
            client,
            new TelemetryMiddlewareOptions());

        await middleware.InvokeAsync(context);
        await client.FlushAsync(TimeSpan.FromSeconds(2));

        List<JsonElement> signals = collector.Records("signal");
        Assert.Single(signals);
        Assert.Equal("10.77.0.5", signals[0].GetProperty("peer_ip").GetString());
        Assert.True(signals[0].GetProperty("synthetic").GetBoolean());
    }

    [Fact]
    public async Task ABodyIsStillReadableByTheApplicationAfterItHasBeenDescribed()
    {
        FakeCollector collector = new();
        await using TelemetryClient client = Client(collector);

        byte[] payload = Encoding.UTF8.GetBytes("{\"name\":\"casting-run-114\",\"quantity\":40}");
        DefaultHttpContext context = Request("10.88.0.31");
        context.Request.Method = "POST";
        context.Request.ContentType = "application/json";
        context.Request.ContentLength = payload.Length;
        context.Request.Body = new System.IO.MemoryStream(payload);

        string seenByTheApplication = string.Empty;
        TelemetryMiddleware middleware = new(
            async ctx =>
            {
                using System.IO.StreamReader reader = new(ctx.Request.Body);
                seenByTheApplication = await reader.ReadToEndAsync();
            },
            client,
            new TelemetryMiddlewareOptions());

        await middleware.InvokeAsync(context);
        await client.FlushAsync(TimeSpan.FromSeconds(2));

        Assert.Equal("{\"name\":\"casting-run-114\",\"quantity\":40}", seenByTheApplication);
        JsonElement record = Assert.Single(collector.Records("http_request"));
        List<string> names = new();
        foreach (JsonElement param in record.GetProperty("params").EnumerateArray())
        {
            names.Add(param.GetProperty("in").GetString() + ":" + param.GetProperty("name").GetString());
        }

        Assert.Contains("json:name", names);
        Assert.Contains("json:quantity", names);
    }
}
