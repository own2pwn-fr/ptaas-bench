using System;
using System.Collections.Generic;
using System.Net;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.AspNetCore.Http;
using Xunit;

namespace Internal.Telemetry.Tests;

/// <summary>
/// A counter raised deep inside a service has to inherit the facts of the request that
/// provoked it. If it does not, work the estate's own probes cause looks like organic
/// traffic and is counted as such, which is exactly the confusion the classification
/// exists to prevent.
/// </summary>
public sealed class ContextTests
{
    private static TelemetryClient Client(FakeCollector collector)
    {
        return new TelemetryClient(new TelemetryOptions
        {
            Service = "portal",
            Endpoint = "http://collector.internal:8900",
            SyntheticCidrs = new[] { "10.77.0.0/24" },
            MessageHandler = collector,
            EnvironmentReader = _ => null,
        });
    }

    private static DefaultHttpContext Request(string peer)
    {
        DefaultHttpContext context = new();
        context.Request.Method = "POST";
        context.Request.Path = "/api/documents/import";
        context.Connection.RemoteIpAddress = IPAddress.Parse(peer);
        return context;
    }

    [Fact]
    public async Task TheAmbientFactsSurviveATaskRunBoundary()
    {
        // The case worth asserting: a handler hands work to the thread pool, and the
        // counter is raised from there. Execution context flows into Task.Run, so the
        // address and the classification must arrive with it.
        FakeCollector collector = new();
        await using TelemetryClient client = Client(collector);

        TelemetryMiddleware middleware = new(
            async _ =>
            {
                await Task.Run(async () =>
                {
                    await Task.Yield();
                    await Task.Run(() =>
                        client.Signal("portal.directory.import.entity_resolved", detail: "two pools deep"));
                });
            },
            client,
            new TelemetryMiddlewareOptions());

        await middleware.InvokeAsync(Request("10.77.0.5"));
        await client.FlushAsync(TimeSpan.FromSeconds(2));

        JsonElement signal = Assert.Single(collector.Records("signal"));
        Assert.Equal("10.77.0.5", signal.GetProperty("peer_ip").GetString());
        Assert.True(signal.GetProperty("synthetic").GetBoolean());
    }

    [Fact]
    public async Task TheAmbientFactsSurviveAnAwaitedContinuationOnAnotherThread()
    {
        FakeCollector collector = new();
        await using TelemetryClient client = Client(collector);

        TelemetryMiddleware middleware = new(
            async _ =>
            {
                await Task.Delay(5).ConfigureAwait(false);
                client.Signal("portal.directory.import.entity_resolved", detail: "after a continuation");
            },
            client,
            new TelemetryMiddlewareOptions());

        await middleware.InvokeAsync(Request("10.88.0.44"));
        await client.FlushAsync(TimeSpan.FromSeconds(2));

        JsonElement signal = Assert.Single(collector.Records("signal"));
        Assert.Equal("10.88.0.44", signal.GetProperty("peer_ip").GetString());
        Assert.False(signal.TryGetProperty("synthetic", out JsonElement marker) && marker.GetBoolean());
    }

    [Fact]
    public async Task TheHttpContextIsASecondSourceWhenTheAmbientStoreWasLost()
    {
        // Work started with the execution context suppressed does not carry the ambient
        // store. The context filed on the request is what keeps such a counter attached.
        FakeCollector collector = new();
        await using TelemetryClient client = Client(collector);
        DefaultHttpContext context = Request("10.77.0.5");

        RequestContext? recovered = null;
        TelemetryMiddleware middleware = new(
            ctx =>
            {
                using (ExecutionContext.SuppressFlow())
                {
                    Task work = Task.Run(() =>
                    {
                        Assert.Null(TelemetryContext.Current);
                        recovered = ctx.Items[TelemetryContext.ItemsKey] as RequestContext;
                    });
                    work.GetAwaiter().GetResult();
                }

                return Task.CompletedTask;
            },
            client,
            new TelemetryMiddlewareOptions());

        await middleware.InvokeAsync(context);

        Assert.NotNull(recovered);
        Assert.Equal("10.77.0.5", recovered!.PeerIp);
        Assert.True(recovered.Synthetic);
    }

    [Fact]
    public async Task BindCarriesTheFactsWhereTheExecutionContextDoesNotFollow()
    {
        // The one boundary the ambient store does not cross on its own. Measured rather
        // than assumed, because the failure is silent: the counter is still raised, it
        // just arrives with no address and is then treated as organic traffic.
        FakeCollector collector = new();
        await using TelemetryClient client = Client(collector);

        TelemetryMiddleware middleware = new(
            _ =>
            {
                Action work = TelemetryContext.Bind(() =>
                    client.Signal("portal.directory.import.entity_resolved", detail: "bound across a boundary"));
                using (ExecutionContext.SuppressFlow())
                {
                    Task.Run(work).GetAwaiter().GetResult();
                }

                return Task.CompletedTask;
            },
            client,
            new TelemetryMiddlewareOptions());

        await middleware.InvokeAsync(Request("10.77.0.5"));
        await client.FlushAsync(TimeSpan.FromSeconds(2));

        JsonElement signal = Assert.Single(collector.Records("signal"));
        Assert.Equal("10.77.0.5", signal.GetProperty("peer_ip").GetString());
        Assert.True(signal.GetProperty("synthetic").GetBoolean());
    }

    [Fact]
    public void ASignalNameThatIsNotMetricShapedIsRefusedOnTheLinkPathToo()
    {
        // The failure this guards against is silent at both ends: a name the collector
        // will drop, sent by a client that did not object, makes a blind flaw look
        // unexploited to everyone.
        TelemetryClient client = new(new TelemetryOptions
        {
            Service = "portal",
            Endpoint = "http://collector.internal:8900",
            EnvironmentReader = _ => null,
        });

        client.Outbound("http://example.test/x", "two.segments");
        client.Outbound("http://example.test/x", "Portal.Bad.Name");
        Assert.Equal(2, client.RejectedNames);

        client.Outbound("http://example.test/x", "portal.directory.import.entity_resolved");
        Assert.Equal(2, client.RejectedNames);
    }

    [Fact]
    public void TheAmbientStoreIsEmptyOutsideARequest()
    {
        TelemetryContext.Clear();
        TelemetryContext.Resolver = null;
        Assert.Null(TelemetryContext.Current);

        RequestContext scoped = new() { RequestId = "abc", Synthetic = true };
        TelemetryContext.Run(scoped, () => Assert.Same(scoped, TelemetryContext.Current));
        Assert.Null(TelemetryContext.Current);
    }

    [Fact]
    public async Task ADependencyLinkIsPostedImmediatelyAndCarriesTheRequestFacts()
    {
        FakeCollector collector = new();
        await using TelemetryClient client = Client(collector);

        string id = string.Empty;
        TelemetryMiddleware middleware = new(
            _ =>
            {
                id = client.Outbound(
                    "http://feeds.partner-supply.example/catalogue.xml",
                    "portal.directory.import.entity_resolved",
                    param: "source");
                return Task.CompletedTask;
            },
            client,
            new TelemetryMiddlewareOptions());

        await middleware.InvokeAsync(Request("10.88.0.44"));
        await client.FlushAsync(TimeSpan.FromSeconds(2));

        List<JsonElement> links = collector.Links();
        Assert.Single(links);
        Assert.Equal("feeds.partner-supply.example", links[0].GetProperty("destination_host").GetString());
        Assert.Equal("source", links[0].GetProperty("param").GetString());
        Assert.Equal("10.88.0.44", links[0].GetProperty("peer_ip").GetString());
        Assert.Equal(id, links[0].GetProperty("request_id").GetString());
    }

    [Fact]
    public void AnOutboundDestinationIsReducedToItsHost()
    {
        Assert.Equal("9f2c.example.test", TelemetryClient.HostOf("http://9f2c.example.test:8080/a/b?c=d"));
        Assert.Equal("9f2c.example.test", TelemetryClient.HostOf("9f2c.example.test:8080/a"));
        Assert.Equal("169.254.169.254", TelemetryClient.HostOf("http://169.254.169.254/latest/meta-data/"));
        Assert.Equal("::1", TelemetryClient.HostOf("[::1]:9000"));
        Assert.Equal(string.Empty, TelemetryClient.HostOf(null));
    }
}
