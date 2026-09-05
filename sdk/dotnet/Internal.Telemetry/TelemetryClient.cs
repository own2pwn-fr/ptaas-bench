using System;
using System.Collections.Generic;
using System.Text.RegularExpressions;
using System.Threading.Tasks;

namespace Internal.Telemetry;

/// <summary>
/// The handle an application talks to.
/// </summary>
/// <remarks>
/// Every public member is total: it swallows its own errors and returns without
/// complaint. Instrumentation that can fail a request is worse than no instrumentation,
/// so the worst outcome here is a missing data point.
/// </remarks>
public sealed class TelemetryClient : IAsyncDisposable
{
    /// <summary>
    /// Signal names are metric names: lower case, dotted, at least three segments, each
    /// starting with a letter. Validated locally because the collector applies the same
    /// rule and drops what does not match, and a name that creates a series no chart is
    /// watching is worse than no name at all.
    /// </summary>
    public static readonly Regex SignalNamePattern =
        new("^[a-z][a-z0-9]*(\\.[a-z][a-z0-9_]*){2,}$", RegexOptions.CultureInvariant);

    private const int TextMax = 1024;

    private readonly Transport _transport;
    private long _rejectedNames;

    /// <summary>Build a client from options, falling back to the environment.</summary>
    /// <param name="options">Explicit options, or null to read the environment alone.</param>
    public TelemetryClient(TelemetryOptions? options = null)
        : this(TelemetryConfig.Resolve(options))
    {
    }

    /// <summary>Build a client on an already resolved configuration.</summary>
    /// <param name="config">The configuration.</param>
    public TelemetryClient(TelemetryConfig config)
    {
        Config = config ?? throw new ArgumentNullException(nameof(config));
        _transport = new Transport(Config);
    }

    /// <summary>The configuration this client runs on.</summary>
    public TelemetryConfig Config { get; }

    /// <summary>False makes every entry point a no-op.</summary>
    public bool Enabled => Config.Enabled;

    /// <summary>Service name stamped on every record.</summary>
    public string Service => Config.Service;

    /// <summary>Names rejected for not being metric-shaped.</summary>
    public long RejectedNames => System.Threading.Interlocked.Read(ref _rejectedNames);

    /// <summary>
    /// Queue an already-built record, stamping the service, the time and the ambient
    /// request facts when they were left out.
    /// </summary>
    /// <param name="record">The record.</param>
    public void Emit(TelemetryEvent record)
    {
        if (!Enabled || record is null)
        {
            return;
        }

        try
        {
            if (string.IsNullOrEmpty(record.App))
            {
                record.App = Config.Service;
            }

            record.Timestamp ??= Transport.UnixSeconds();

            // A record raised inside a handler inherits the request it belongs to, so a
            // counter incremented three call frames down still says who provoked it.
            // An explicit value always wins: the middleware read the connection itself.
            RequestContext? context = TelemetryContext.Current;
            if (context is not null)
            {
                record.PeerIp ??= context.PeerIp;
                record.ClientIp ??= context.ClientIp;
                if (record.Synthetic is null && context.Synthetic)
                {
                    record.Synthetic = true;
                }
            }

            _transport.Enqueue(record);
        }
        catch (Exception)
        {
            // Unreachable in practice; kept because "never throws" is a property this
            // class is relied on for, not an aspiration.
        }
    }

    /// <summary>
    /// Record an application-level anomaly.
    /// </summary>
    /// <remarks>
    /// Raise this where the anomalous EFFECT is confirmed, never where a suspicious
    /// input arrives. A counter that also counts inputs which turned out to be inert is
    /// dominated by noise within a day and stops being usable as an alert.
    /// </remarks>
    /// <param name="name">Dotted metric name, e.g. portal.documents.write.path_escape.</param>
    /// <param name="payload">The input that produced the anomaly.</param>
    /// <param name="detail">What was actually observed, in a form a human can act on.</param>
    /// <param name="requestId">Correlation id, when the caller tracks one.</param>
    /// <param name="synthetic">Force the classification, for a probe exercising its own path.</param>
    public void Signal(
        string name,
        string? payload = null,
        string? detail = null,
        string? requestId = null,
        bool? synthetic = null)
    {
        if (!Enabled)
        {
            return;
        }

        try
        {
            if (string.IsNullOrEmpty(name) || !SignalNamePattern.IsMatch(name))
            {
                System.Threading.Interlocked.Increment(ref _rejectedNames);
                return;
            }

            RequestContext? context = TelemetryContext.Current;
            SignalEvent record = new()
            {
                App = Config.Service,
                Timestamp = Transport.UnixSeconds(),
                Signal = name,
                Attributes = new SignalAttributes
                {
                    Payload = Clip(payload),
                    Detail = Clip(detail),
                    RequestId = Clip(requestId ?? context?.RequestId),
                },
            };

            if (synthetic.HasValue)
            {
                record.Synthetic = synthetic.Value;
            }

            Emit(record);
        }
        catch (Exception)
        {
            // See the class remark.
        }
    }

    /// <summary>Free-form breadcrumb, kept beside the records of the same period.</summary>
    /// <param name="message">The message.</param>
    /// <param name="synthetic">Force the classification.</param>
    public void Note(string message, bool? synthetic = null)
    {
        if (!Enabled)
        {
            return;
        }

        try
        {
            NoteEvent record = new()
            {
                App = Config.Service,
                Timestamp = Transport.UnixSeconds(),
                Message = Clip(message),
            };
            if (synthetic.HasValue)
            {
                record.Synthetic = synthetic.Value;
            }

            Emit(record);
        }
        catch (Exception)
        {
            // See the class remark.
        }
    }

    /// <summary>
    /// Register an outbound dependency call whose destination came from a request.
    /// </summary>
    /// <remarks>
    /// <para>
    /// Call it immediately BEFORE the fetch. A request-controlled destination means the
    /// resulting egress - a name lookup, a connection, a hit on some third party -
    /// appears in the network's own logs with nothing tying it back to the request that
    /// caused it. Registering the pairing beforehand is what lets the two be joined.
    /// </para>
    /// <para>
    /// Dispatch is immediate and on its own connection rather than through the record
    /// queue: the name lookup follows within microseconds, and anything that waited for
    /// the next export would arrive after the effect it explains. Immediate still means
    /// off the request path - the send is started, never awaited.
    /// </para>
    /// </remarks>
    /// <param name="destination">The URL or host about to be resolved.</param>
    /// <param name="signal">The code path making the call, same vocabulary as a signal name.</param>
    /// <param name="param">Name of the input the destination came from.</param>
    /// <param name="route">Route template being served; taken from the request when omitted.</param>
    /// <param name="requestId">Correlation id; taken from the request, or generated.</param>
    /// <param name="synthetic">Force the classification.</param>
    /// <returns>The correlation id, so the caller can attach it to later records.</returns>
    public string Outbound(
        string destination,
        string signal,
        string? param = null,
        string? route = null,
        string? requestId = null,
        bool? synthetic = null)
    {
        RequestContext? context = TelemetryContext.Current;
        string id = requestId ?? context?.RequestId ?? Guid.NewGuid().ToString("n");
        if (!Enabled)
        {
            return id;
        }

        try
        {
            OutboundLink link = new()
            {
                App = Config.Service,
                Timestamp = Transport.UnixSeconds(),
                DestinationHost = HostOf(destination),
                Param = param,
                Route = route ?? context?.Route,
                RequestId = id,
                PeerIp = context?.PeerIp,
                ClientIp = context?.ClientIp,
            };

            if (!string.IsNullOrEmpty(signal) && SignalNamePattern.IsMatch(signal))
            {
                link.Signal = signal;
            }
            else
            {
                System.Threading.Interlocked.Increment(ref _rejectedNames);
            }

            bool isSynthetic = synthetic ?? context?.Synthetic ?? false;
            if (isSynthetic)
            {
                link.Synthetic = true;
            }

            _transport.DispatchLink(link);
        }
        catch (Exception)
        {
            // See the class remark.
        }

        return id;
    }

    /// <summary>Declare the authenticated principal of the request in flight.</summary>
    /// <param name="subject">The principal, or null.</param>
    public void SetAuthSubject(string? subject)
    {
        RequestContext? context = TelemetryContext.Current;
        if (context is not null)
        {
            context.AuthSubject = subject;
        }
    }

    /// <summary>
    /// Contribute extra described inputs to the record of the request in flight, so one
    /// request stays one record however many helpers were called.
    /// </summary>
    /// <param name="entries">Entries to merge.</param>
    public void AddRequestParams(IEnumerable<ParamEntry>? entries)
    {
        if (entries is null)
        {
            return;
        }

        RequestContext? context = TelemetryContext.Current;
        if (context is null)
        {
            return;
        }

        foreach (ParamEntry entry in entries)
        {
            if (context.ExtraParams.Count >= Config.MaxAttributes)
            {
                return;
            }

            context.ExtraParams.Add(entry);
        }
    }

    /// <summary>A collector sized by this client's configuration.</summary>
    /// <returns>A fresh collector.</returns>
    public ParamCollector NewParamCollector() => new(Config.MaxAttributes);

    /// <summary>Exporter counters.</summary>
    /// <returns>A snapshot.</returns>
    public TransportStats Stats() => _transport.Stats();

    /// <summary>Wait for the queue to drain. Shutdown hooks and tests only.</summary>
    /// <param name="timeout">How long to wait, or null for two seconds.</param>
    /// <returns>True when everything queued was handed over.</returns>
    public Task<bool> FlushAsync(TimeSpan? timeout = null)
    {
        return _transport.FlushAsync(timeout ?? TimeSpan.FromSeconds(2));
    }

    /// <summary>Stop the exporter and drain what is left.</summary>
    /// <returns>A task completing when the exporter has stopped.</returns>
    public async ValueTask DisposeAsync()
    {
        await _transport.DisposeAsync().ConfigureAwait(false);
    }

    /// <summary>True when the address sits in one of the generated-traffic ranges.</summary>
    /// <remarks>
    /// The argument must be the socket peer and nothing else. Never a forwarded header,
    /// never a helper that folds one in: any caller can announce an address about
    /// itself, so a decision taken on one is a decision taken by the caller.
    /// </remarks>
    /// <param name="peer">The socket peer.</param>
    /// <returns>True when it is inside a configured range.</returns>
    public bool IsSyntheticPeer(System.Net.IPAddress? peer) => Config.SyntheticSources.Matches(peer);

    /// <summary>Host part of a URL, or the value itself when it is already a host.</summary>
    /// <param name="destination">A URL or a host.</param>
    /// <returns>The bare host, lower case.</returns>
    public static string HostOf(string? destination)
    {
        string text = (destination ?? string.Empty).Trim();
        if (text.Length == 0)
        {
            return string.Empty;
        }

        if (Uri.TryCreate(text, UriKind.Absolute, out Uri? uri) && !string.IsNullOrEmpty(uri.Host))
        {
            return uri.Host.ToLowerInvariant();
        }

        string head = text.Split('/')[0].Split('?')[0];
        int at = head.LastIndexOf('@');
        if (at >= 0)
        {
            head = head.Substring(at + 1);
        }

        if (head.StartsWith("[", StringComparison.Ordinal))
        {
            int close = head.IndexOf(']');
            if (close > 1)
            {
                return head.Substring(1, close - 1).ToLowerInvariant();
            }
        }

        int colon = head.IndexOf(':');
        if (colon >= 0)
        {
            head = head.Substring(0, colon);
        }

        return head.ToLowerInvariant();
    }

    private static string? Clip(string? value)
    {
        if (value is null)
        {
            return null;
        }

        return value.Length > TextMax ? value.Substring(0, TextMax) : value;
    }
}
