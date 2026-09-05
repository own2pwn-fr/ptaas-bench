using System;
using System.Collections.Generic;
using System.Globalization;
using System.Net.Http;

namespace Internal.Telemetry;

/// <summary>
/// The resolved configuration one client runs on. Built once, then read-only, so no
/// request path ever parses an environment variable.
/// </summary>
public sealed class TelemetryConfig
{
    /// <summary>The collector refuses a batch larger than this.</summary>
    public const int MaxBatch = 500;

    private TelemetryConfig(
        string service,
        string? endpoint,
        bool enabled,
        string eventsPath,
        string correlationsPath,
        int batchSize,
        int maxQueueSize,
        TimeSpan requestTimeout,
        SourceMatcher syntheticSources,
        int maxBodyBytes,
        int maxAttributes,
        int maxBodyDepth,
        bool reportDiscards,
        HttpMessageHandler? messageHandler)
    {
        Service = service;
        Endpoint = endpoint;
        Enabled = enabled;
        EventsPath = eventsPath;
        CorrelationsPath = correlationsPath;
        BatchSize = batchSize;
        MaxQueueSize = maxQueueSize;
        RequestTimeout = requestTimeout;
        SyntheticSources = syntheticSources;
        MaxBodyBytes = maxBodyBytes;
        MaxAttributes = maxAttributes;
        MaxBodyDepth = maxBodyDepth;
        ReportDiscards = reportDiscards;
        MessageHandler = messageHandler;
    }

    /// <summary>Service name stamped on every record.</summary>
    public string Service { get; }

    /// <summary>Collector base address, or null when none was configured.</summary>
    public string? Endpoint { get; }

    /// <summary>False makes every entry point a no-op.</summary>
    public bool Enabled { get; }

    /// <summary>Path batched records are posted to.</summary>
    public string EventsPath { get; }

    /// <summary>Path dependency links are posted to.</summary>
    public string CorrelationsPath { get; }

    /// <summary>Records per POST.</summary>
    public int BatchSize { get; }

    /// <summary>Records held in memory before the oldest are dropped.</summary>
    public int MaxQueueSize { get; }

    /// <summary>Budget for one export call.</summary>
    public TimeSpan RequestTimeout { get; }

    /// <summary>Compiled prefixes for the estate's own generated traffic.</summary>
    public SourceMatcher SyntheticSources { get; }

    /// <summary>Request body bytes buffered for attribute extraction.</summary>
    public int MaxBodyBytes { get; }

    /// <summary>Attributes recorded per record.</summary>
    public int MaxAttributes { get; }

    /// <summary>Nesting depth walked when flattening a JSON document.</summary>
    public int MaxBodyDepth { get; }

    /// <summary>Whether dropped records are announced as a note.</summary>
    public bool ReportDiscards { get; }

    /// <summary>Transport seam; null means an ordinary socket handler.</summary>
    public HttpMessageHandler? MessageHandler { get; }

    /// <summary>
    /// Resolve options against the environment. Explicit values always win; anything
    /// left unset falls back to a TELEMETRY_* variable and then to a default.
    /// </summary>
    public static TelemetryConfig Resolve(TelemetryOptions? options = null)
    {
        options ??= new TelemetryOptions();
        Func<string, string?> env = options.EnvironmentReader ?? Environment.GetEnvironmentVariable;

        string service = options.Service ?? env("TELEMETRY_SERVICE") ?? string.Empty;
        string endpointRaw = options.Endpoint ?? env("TELEMETRY_ENDPOINT") ?? string.Empty;
        string? endpoint = endpointRaw.Length == 0 ? null : endpointRaw.TrimEnd('/');

        // With no service name nothing is configured, so the client stays inert. That
        // keeps it quiet in local development and in unit tests with no extra wiring.
        bool enabled = options.Enabled ?? ParseBool(env("TELEMETRY_ENABLED")) ?? service.Length > 0;

        IReadOnlyList<string> cidrs = options.SyntheticCidrs ?? SplitList(env("TELEMETRY_SYNTHETIC_CIDRS"));

        int batchSize = Clamp(options.BatchSize ?? ParseInt(env("TELEMETRY_BATCH_MAX")) ?? MaxBatch, 1, MaxBatch);
        int queueMax = Math.Max(1, options.MaxQueueSize ?? ParseInt(env("TELEMETRY_QUEUE_MAX")) ?? 10_000);
        TimeSpan timeout = options.RequestTimeout
            ?? TimeSpan.FromSeconds(ParseDouble(env("TELEMETRY_TIMEOUT_S")) ?? 5.0);
        if (timeout <= TimeSpan.Zero)
        {
            timeout = TimeSpan.FromSeconds(5);
        }

        return new TelemetryConfig(
            service: service,
            endpoint: endpoint,
            enabled: enabled && service.Length > 0,
            eventsPath: WithLeadingSlash(options.EventsPath ?? env("TELEMETRY_EVENTS_PATH") ?? "/v1/traces"),
            correlationsPath: WithLeadingSlash(
                options.CorrelationsPath ?? env("TELEMETRY_CORRELATIONS_PATH") ?? "/v1/correlations"),
            batchSize: batchSize,
            maxQueueSize: queueMax,
            requestTimeout: timeout,
            syntheticSources: SourceMatcher.Compile(cidrs),
            maxBodyBytes: Math.Max(0, options.MaxBodyBytes ?? ParseInt(env("TELEMETRY_MAX_BODY_BYTES")) ?? 262_144),
            maxAttributes: Math.Max(1, options.MaxAttributes ?? ParseInt(env("TELEMETRY_MAX_PARAMS")) ?? 1024),
            maxBodyDepth: Math.Max(1, options.MaxBodyDepth ?? 16),
            reportDiscards: options.ReportDiscards ?? true,
            messageHandler: options.MessageHandler);
    }

    private static int Clamp(int value, int low, int high)
    {
        if (value < low)
        {
            return low;
        }

        return value > high ? high : value;
    }

    private static string WithLeadingSlash(string path)
    {
        if (string.IsNullOrEmpty(path))
        {
            return "/";
        }

        return path[0] == '/' ? path : "/" + path;
    }

    private static bool? ParseBool(string? raw)
    {
        if (string.IsNullOrWhiteSpace(raw))
        {
            return null;
        }

        switch (raw.Trim().ToLowerInvariant())
        {
            case "0":
            case "false":
            case "no":
            case "off":
                return false;
            case "1":
            case "true":
            case "yes":
            case "on":
                return true;
            default:
                return null;
        }
    }

    private static int? ParseInt(string? raw)
    {
        if (string.IsNullOrWhiteSpace(raw))
        {
            return null;
        }

        return int.TryParse(raw.Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out int value)
            ? value
            : null;
    }

    private static double? ParseDouble(string? raw)
    {
        if (string.IsNullOrWhiteSpace(raw))
        {
            return null;
        }

        return double.TryParse(raw.Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out double value)
            ? value
            : null;
    }

    private static IReadOnlyList<string> SplitList(string? raw)
    {
        if (string.IsNullOrWhiteSpace(raw))
        {
            return Array.Empty<string>();
        }

        List<string> parts = new();
        foreach (string chunk in raw.Split(new[] { ',', ' ', '\t', '\n', '\r' }, StringSplitOptions.RemoveEmptyEntries))
        {
            string trimmed = chunk.Trim();
            if (trimmed.Length > 0)
            {
                parts.Add(trimmed);
            }
        }

        return parts;
    }
}
