using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Net;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;
using Microsoft.AspNetCore.Routing.Patterns;
using Xunit;

namespace Internal.Telemetry.Tests;

/// <summary>
/// Route templates, and the properties of the exporter that keep it invisible from a
/// served request.
/// </summary>
public sealed class RouteAndTransportTests
{
    private static TelemetryClient Client(FakeCollector? collector, string? endpoint = "http://collector.internal:8900")
    {
        return new TelemetryClient(new TelemetryOptions
        {
            Service = "portal",
            Endpoint = endpoint,
            MessageHandler = collector,
            EnvironmentReader = _ => null,
        });
    }

    private static DefaultHttpContext WithEndpoint(string pattern, string path)
    {
        DefaultHttpContext context = new();
        context.Request.Method = "GET";
        context.Request.Path = path;
        context.Connection.RemoteIpAddress = IPAddress.Parse("10.88.0.31");
        RouteEndpoint endpoint = new(
            _ => Task.CompletedTask,
            RoutePatternFactory.Parse(pattern),
            0,
            null,
            pattern);
        context.SetEndpoint(endpoint);
        return context;
    }

    [Fact]
    public void TheTemplateIsReportedRatherThanTheConcreteUrl()
    {
        DefaultHttpContext context = WithEndpoint("/api/orders/{id}", "/api/orders/4102");
        Assert.Equal("/api/orders/{id}", TelemetryMiddleware.RouteTemplate(context));
    }

    [Fact]
    public void ATemplateRegisteredWithoutALeadingSlashIsNormalised()
    {
        // Razor Pages registers its patterns without one; the minimal routing APIs keep
        // one. Reporting both forms would put a single endpoint under two names.
        DefaultHttpContext context = WithEndpoint("help/directory", "/help/directory");
        Assert.Equal("/help/directory", TelemetryMiddleware.RouteTemplate(context));
    }

    [Fact]
    public void AnUnroutedRequestIsReportedAsUnmatched()
    {
        DefaultHttpContext context = new();
        context.Request.Path = "/assets/site.css";
        Assert.Equal("<unmatched>", TelemetryMiddleware.RouteTemplate(context));
        Assert.Equal(TelemetryRoute.Unmatched, TelemetryMiddleware.RouteTemplate(context));
    }

    [Fact]
    public async Task TheRecordCarriesTheTemplateTheRouteValuesAndTheStatus()
    {
        FakeCollector collector = new();
        await using TelemetryClient client = Client(collector);
        DefaultHttpContext context = WithEndpoint("/api/orders/{id}", "/api/orders/4102");
        context.Request.RouteValues["id"] = "4102";

        TelemetryMiddleware middleware = new(
            ctx =>
            {
                ctx.Response.StatusCode = 404;
                return Task.CompletedTask;
            },
            client,
            new TelemetryMiddlewareOptions());

        await middleware.InvokeAsync(context);
        await client.FlushAsync(TimeSpan.FromSeconds(2));

        JsonElement record = Assert.Single(collector.Records("http_request"));
        Assert.Equal("/api/orders/{id}", record.GetProperty("route").GetString());
        Assert.Equal("/api/orders/4102", record.GetProperty("path").GetString());
        Assert.Equal(404, record.GetProperty("status").GetInt32());

        bool sawRouteValue = false;
        foreach (JsonElement param in record.GetProperty("params").EnumerateArray())
        {
            if (param.GetProperty("in").GetString() == "path" && param.GetProperty("name").GetString() == "id")
            {
                sawRouteValue = true;
            }
        }

        Assert.True(sawRouteValue);
    }

    [Fact]
    public async Task ExactlyOneRecordIsExportedPerRequest()
    {
        FakeCollector collector = new();
        await using TelemetryClient client = Client(collector);
        TelemetryMiddleware middleware = new(_ => Task.CompletedTask, client, new TelemetryMiddlewareOptions());

        await middleware.InvokeAsync(WithEndpoint("/help", "/help"));
        await middleware.InvokeAsync(WithEndpoint("/help", "/help"));
        await client.FlushAsync(TimeSpan.FromSeconds(2));

        Assert.Equal(2, collector.Records("http_request").Count);
    }

    [Fact]
    public async Task AHandlerThatThrowsStillProducesExactlyOneRecordAndTheErrorStillEscapes()
    {
        FakeCollector collector = new();
        await using TelemetryClient client = Client(collector);
        TelemetryMiddleware middleware = new(
            _ => throw new InvalidOperationException("boom"),
            client,
            new TelemetryMiddlewareOptions());

        await Assert.ThrowsAsync<InvalidOperationException>(
            () => middleware.InvokeAsync(WithEndpoint("/reports", "/reports")));
        await client.FlushAsync(TimeSpan.FromSeconds(2));

        Assert.Single(collector.Records("http_request"));
    }

    [Fact]
    public async Task NothingIsAddedToTheResponse()
    {
        // No header, no body, no status change. A client cannot tell the difference
        // between a service that loads this package and one that does not.
        FakeCollector collector = new();
        await using TelemetryClient client = Client(collector);
        DefaultHttpContext context = WithEndpoint("/help", "/help");
        int headersBefore = context.Response.Headers.Count;

        TelemetryMiddleware middleware = new(_ => Task.CompletedTask, client, new TelemetryMiddlewareOptions());
        await middleware.InvokeAsync(context);

        Assert.Equal(headersBefore, context.Response.Headers.Count);
        Assert.Equal(200, context.Response.StatusCode);
    }

    [Fact]
    public async Task AnUnreachableCollectorCostsTheRequestNothing()
    {
        FakeCollector collector = new() { Unreachable = true, Delay = TimeSpan.FromSeconds(3) };
        await using TelemetryClient client = Client(collector);
        TelemetryMiddleware middleware = new(_ => Task.CompletedTask, client, new TelemetryMiddlewareOptions());

        // Warm the paths so compilation is not billed to the measurement.
        for (int i = 0; i < 200; i++)
        {
            await middleware.InvokeAsync(WithEndpoint("/help", "/help"));
        }

        // Contexts are built ahead of the measurement so that what is timed is the
        // middleware and nothing else.
        List<DefaultHttpContext> contexts = new(500);
        for (int i = 0; i < 500; i++)
        {
            contexts.Add(WithEndpoint("/help", "/help"));
        }

        Stopwatch watch = Stopwatch.StartNew();
        foreach (DefaultHttpContext prepared in contexts)
        {
            await middleware.InvokeAsync(prepared);
        }

        watch.Stop();
        double perRequestMs = watch.Elapsed.TotalMilliseconds / contexts.Count;
        Assert.True(perRequestMs < 1.0, "middleware cost " + perRequestMs + " ms per request");
    }

    [Fact]
    public async Task AClientWithNoCollectorConfiguredStillNeverThrows()
    {
        await using TelemetryClient client = Client(null, endpoint: null);
        client.Signal("portal.documents.write.path_escape", detail: "no collector configured");
        client.Note("starting up");
        string id = client.Outbound("http://example.test/x", "portal.documents.write.path_escape");
        Assert.False(string.IsNullOrEmpty(id));
    }

    [Fact]
    public void AnInertClientIsSilentAndStillTotal()
    {
        // No service name means nothing is configured, which is the state in local
        // development and in unit tests that never wire anything up.
        TelemetryClient client = new(new TelemetryOptions { EnvironmentReader = _ => null });
        Assert.False(client.Enabled);
        client.Signal("portal.documents.write.path_escape");
        Assert.Equal(0, client.Stats().Enqueued);
    }

    [Fact]
    public void ANameThatIsNotMetricShapedIsCountedAndDropped()
    {
        TelemetryClient client = new(new TelemetryOptions
        {
            Service = "portal",
            EnvironmentReader = _ => null,
        });

        client.Signal("Portal.Bad.Name");
        client.Signal("tooshort");
        client.Signal("portal.only_two");
        Assert.Equal(3, client.RejectedNames);

        client.Signal("portal.documents.write.path_escape");
        Assert.Equal(3, client.RejectedNames);
    }

    [Fact]
    public async Task TheQueueDropsTheOldestRecordsAndCountsThem()
    {
        FakeCollector collector = new() { Delay = TimeSpan.FromMilliseconds(400) };
        await using TelemetryClient client = new(new TelemetryOptions
        {
            Service = "portal",
            Endpoint = "http://collector.internal:8900",
            MaxQueueSize = 8,
            BatchSize = 4,
            MessageHandler = collector,
            EnvironmentReader = _ => null,
        });

        for (int i = 0; i < 200; i++)
        {
            client.Note("record " + i);
        }

        TransportStats stats = client.Stats();
        Assert.True(stats.Dropped > 0, "expected the bound to have been reached");
        Assert.True(stats.Queued <= 8);
    }

    [Fact]
    public void ConfigurationFallsBackToTheEnvironmentAndThenToDefaults()
    {
        Dictionary<string, string> environment = new(StringComparer.Ordinal)
        {
            ["TELEMETRY_SERVICE"] = "portal",
            ["TELEMETRY_ENDPOINT"] = "http://collector.internal:8900/",
            ["TELEMETRY_SYNTHETIC_CIDRS"] = "10.77.0.0/24, 10.77.1.0/24",
            ["TELEMETRY_QUEUE_MAX"] = "64",
        };

        TelemetryConfig config = TelemetryConfig.Resolve(new TelemetryOptions
        {
            EnvironmentReader = name => environment.TryGetValue(name, out string? value) ? value : null,
        });

        Assert.Equal("portal", config.Service);
        Assert.Equal("http://collector.internal:8900", config.Endpoint);
        Assert.True(config.Enabled);
        Assert.Equal("/v1/traces", config.EventsPath);
        Assert.Equal("/v1/correlations", config.CorrelationsPath);
        Assert.Equal(64, config.MaxQueueSize);
        Assert.Equal(2, config.SyntheticSources.Count);
        Assert.Equal(TelemetryConfig.MaxBatch, config.BatchSize);
    }
}
