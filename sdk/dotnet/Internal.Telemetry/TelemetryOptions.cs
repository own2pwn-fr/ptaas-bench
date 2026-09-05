using System;
using System.Collections.Generic;
using System.Net.Http;

namespace Internal.Telemetry;

/// <summary>
/// Everything the client can be told explicitly. Every member is optional: with no
/// options at all the environment decides, which is how the services in the estate are
/// actually configured.
/// </summary>
public sealed class TelemetryOptions
{
    /// <summary>Service name reported with every record. Defaults to TELEMETRY_SERVICE.</summary>
    public string? Service { get; set; }

    /// <summary>Collector base address, e.g. http://otel-collector:8900. Defaults to TELEMETRY_ENDPOINT.</summary>
    public string? Endpoint { get; set; }

    /// <summary>
    /// Master switch. Defaults to TELEMETRY_ENABLED, otherwise on as soon as a service
    /// name is known. When off every entry point is a no-op that still never throws.
    /// </summary>
    public bool? Enabled { get; set; }

    /// <summary>Path batched records are posted to. Defaults to TELEMETRY_EVENTS_PATH.</summary>
    public string? EventsPath { get; set; }

    /// <summary>Path dependency links are posted to. Defaults to TELEMETRY_CORRELATIONS_PATH.</summary>
    public string? CorrelationsPath { get; set; }

    /// <summary>Records per POST. The collector caps a batch at 500 and so does this.</summary>
    public int? BatchSize { get; set; }

    /// <summary>Records held in memory. Past this the oldest are dropped and counted.</summary>
    public int? MaxQueueSize { get; set; }

    /// <summary>Budget for one export call. It runs on the exporter, never on a request.</summary>
    public TimeSpan? RequestTimeout { get; set; }

    /// <summary>
    /// Source ranges belonging to the estate's own generated traffic, as CIDR prefixes.
    /// Defaults to TELEMETRY_SYNTHETIC_CIDRS (comma or whitespace separated).
    /// </summary>
    public IReadOnlyList<string>? SyntheticCidrs { get; set; }

    /// <summary>Request body bytes buffered for attribute extraction.</summary>
    public int? MaxBodyBytes { get; set; }

    /// <summary>Attributes recorded per record, to bound work on pathological bodies.</summary>
    public int? MaxAttributes { get; set; }

    /// <summary>Nesting depth walked when flattening a JSON document.</summary>
    public int? MaxBodyDepth { get; set; }

    /// <summary>
    /// Report dropped records as a note. On by default: a queue overflow nobody hears
    /// about looks exactly like an endpoint that stopped receiving traffic.
    /// </summary>
    public bool? ReportDiscards { get; set; }

    /// <summary>Transport seam for unit tests. Null uses an ordinary socket handler.</summary>
    public HttpMessageHandler? MessageHandler { get; set; }

    /// <summary>Environment reader seam for unit tests. Null reads the real environment.</summary>
    public Func<string, string?>? EnvironmentReader { get; set; }
}
