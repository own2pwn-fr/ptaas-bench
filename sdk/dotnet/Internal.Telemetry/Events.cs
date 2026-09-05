using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace Internal.Telemetry;

/// <summary>
/// One observed input, described rather than stored.
/// </summary>
/// <remarks>
/// Request values routinely carry personal data, credentials and card numbers, so only
/// a digest, a length and a short prefix leave the process. The digest is still enough
/// for the collector to tell an endpoint called with its documented default value from
/// one called with something else.
/// </remarks>
public sealed class ParamEntry
{
    /// <summary>Input name, dotted for a nested document field.</summary>
    [JsonPropertyName("name")]
    public string Name { get; set; } = string.Empty;

    /// <summary>Where the input reached the handler from.</summary>
    [JsonPropertyName("in")]
    public string In { get; set; } = string.Empty;

    /// <summary>sha256 of the raw value, hex encoded.</summary>
    [JsonPropertyName("value_sha256")]
    public string? ValueSha256 { get; set; }

    /// <summary>Length of the raw value in UTF-8 bytes.</summary>
    [JsonPropertyName("value_len")]
    public int? ValueLength { get; set; }

    /// <summary>First 256 characters of the raw value, so a human can read a row.</summary>
    [JsonPropertyName("sample")]
    public string? Sample { get; set; }
}

/// <summary>Fields every record carries.</summary>
public abstract class TelemetryEvent
{
    /// <summary>Fix the record kind for a derived record.</summary>
    /// <param name="type">The collector's discriminator for this kind.</param>
    protected TelemetryEvent(string type)
    {
        Type = type;
    }

    /// <summary>
    /// Record kind. Set once by the derived type rather than declared abstract: an
    /// overridden property does not reliably carry the name attribute through
    /// reflection, and a record whose kind serialised under the wrong name is dropped
    /// on ingest as unattributable.
    /// </summary>
    [JsonPropertyName("type")]
    public string Type { get; }

    /// <summary>Service name.</summary>
    [JsonPropertyName("app")]
    public string App { get; set; } = string.Empty;

    /// <summary>Unix epoch seconds with fractions, taken in the service.</summary>
    [JsonPropertyName("ts")]
    public double? Timestamp { get; set; }

    /// <summary>
    /// Address of the peer that opened the connection, and nothing else. This is the
    /// only address anything downstream makes a decision on.
    /// </summary>
    [JsonPropertyName("peer_ip")]
    public string? PeerIp { get; set; }

    /// <summary>Descriptive, and untrusted: it may carry a forwarded value.</summary>
    [JsonPropertyName("client_ip")]
    public string? ClientIp { get; set; }

    /// <summary>True when the peer belongs to the estate's own generated traffic.</summary>
    [JsonPropertyName("synthetic")]
    public bool? Synthetic { get; set; }
}

/// <summary>One served request.</summary>
public sealed class HttpRequestEvent : TelemetryEvent
{
    /// <summary>Create an empty request record.</summary>
    public HttpRequestEvent()
        : base("http_request")
    {
    }

    /// <summary>Request method, upper case.</summary>
    [JsonPropertyName("method")]
    public string Method { get; set; } = "GET";

    /// <summary>
    /// Route template as routing registered it (<c>/api/orders/{id}</c>), never the
    /// concrete URL. Concrete paths would give every identifier its own series and make
    /// per-endpoint latency and error rate unusable within a day.
    /// </summary>
    [JsonPropertyName("route")]
    public string Route { get; set; } = TelemetryRoute.Unmatched;

    /// <summary>Concrete request path, kept alongside the template.</summary>
    [JsonPropertyName("path")]
    public string? Path { get; set; }

    /// <summary>Response status.</summary>
    [JsonPropertyName("status")]
    public int? Status { get; set; }

    /// <summary>Authenticated principal, when there was one.</summary>
    [JsonPropertyName("auth_subject")]
    public string? AuthSubject { get; set; }

    /// <summary>User agent as sent.</summary>
    [JsonPropertyName("user_agent")]
    public string? UserAgent { get; set; }

    /// <summary>Every input the handler could have observed.</summary>
    [JsonPropertyName("params")]
    public List<ParamEntry> Params { get; set; } = new();
}

/// <summary>Free-form context attached to a signal.</summary>
public sealed class SignalAttributes
{
    /// <summary>The input that produced the anomaly.</summary>
    [JsonPropertyName("payload")]
    public string? Payload { get; set; }

    /// <summary>What was actually observed, in a form a human can act on.</summary>
    [JsonPropertyName("detail")]
    public string? Detail { get; set; }

    /// <summary>Correlation id of the request the signal belongs to.</summary>
    [JsonPropertyName("request_id")]
    public string? RequestId { get; set; }
}

/// <summary>An application-level counter: something the service decided was anomalous.</summary>
public sealed class SignalEvent : TelemetryEvent
{
    /// <summary>Create an empty counter record.</summary>
    public SignalEvent()
        : base("signal")
    {
    }

    /// <summary>Dotted metric name, e.g. <c>portal.documents.write.path_escape</c>.</summary>
    [JsonPropertyName("signal")]
    public string Signal { get; set; } = string.Empty;

    /// <summary>Context recorded with the counter.</summary>
    [JsonPropertyName("attributes")]
    public SignalAttributes? Attributes { get; set; }
}

/// <summary>A breadcrumb: a startup step, a state transition, a dropped batch.</summary>
public sealed class NoteEvent : TelemetryEvent
{
    /// <summary>Create an empty breadcrumb.</summary>
    public NoteEvent()
        : base("note")
    {
    }

    /// <summary>The message.</summary>
    [JsonPropertyName("message")]
    public string? Message { get; set; }
}

/// <summary>
/// A dependency link: an outbound request the service is about to make to a destination
/// that came from a caller, posted so that the egress the network observes can be tied
/// back to the request that caused it.
/// </summary>
public sealed class OutboundLink
{
    /// <summary>Service name.</summary>
    [JsonPropertyName("app")]
    public string App { get; set; } = string.Empty;

    /// <summary>Unix epoch seconds with fractions.</summary>
    [JsonPropertyName("ts")]
    public double? Timestamp { get; set; }

    /// <summary>The code path about to make the call, same vocabulary as a signal name.</summary>
    [JsonPropertyName("signal")]
    public string? Signal { get; set; }

    /// <summary>Host the service is about to resolve and connect to.</summary>
    [JsonPropertyName("destination_host")]
    public string DestinationHost { get; set; } = string.Empty;

    /// <summary>Route template of the request that caused the call.</summary>
    [JsonPropertyName("route")]
    public string? Route { get; set; }

    /// <summary>Name of the input the destination came from.</summary>
    [JsonPropertyName("param")]
    public string? Param { get; set; }

    /// <summary>Correlation id, so the two sides can be joined.</summary>
    [JsonPropertyName("request_id")]
    public string? RequestId { get; set; }

    /// <summary>Socket peer of the request that caused the call. Never a header value.</summary>
    [JsonPropertyName("peer_ip")]
    public string? PeerIp { get; set; }

    /// <summary>Descriptive, and untrusted.</summary>
    [JsonPropertyName("client_ip")]
    public string? ClientIp { get; set; }

    /// <summary>True when the peer belongs to the estate's own generated traffic.</summary>
    [JsonPropertyName("synthetic")]
    public bool? Synthetic { get; set; }
}

/// <summary>The batch envelope posted to the collector.</summary>
public sealed class EventBatch
{
    /// <summary>
    /// Declared as <c>object</c> on purpose: the serialiser then writes each item's
    /// runtime type, which is what keeps the fields of a derived record from being
    /// silently dropped.
    /// </summary>
    [JsonPropertyName("events")]
    public List<object> Events { get; set; } = new();
}

/// <summary>Route template constants.</summary>
public static class TelemetryRoute
{
    /// <summary>Reported when routing matched nothing at all.</summary>
    public const string Unmatched = "<unmatched>";
}
