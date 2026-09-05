using System;
using System.Threading;
using System.Threading.Tasks;

namespace Internal.Telemetry;

/// <summary>
/// The process-wide client, and a one-line facade over it.
/// </summary>
/// <remarks>
/// Application code reads as <c>Telemetry.Signal("portal.documents.write.path_escape",
/// detail)</c>: one line at the point of interest, no plumbing. Services that prefer
/// dependency injection resolve <see cref="TelemetryClient"/> instead; both names refer
/// to the same instance once the hosting extensions have run.
/// </remarks>
public static class Telemetry
{
    private static TelemetryClient? _current;
    private static readonly object Gate = new();

    /// <summary>
    /// The active client, built from the environment on first use.
    /// </summary>
    /// <remarks>
    /// Lazy construction matters: code that raises a counter before, or without, an
    /// explicit <see cref="Init"/> must still record rather than throw.
    /// </remarks>
    public static TelemetryClient Current
    {
        get
        {
            TelemetryClient? client = Volatile.Read(ref _current);
            if (client is not null)
            {
                return client;
            }

            lock (Gate)
            {
                _current ??= new TelemetryClient();
                return _current;
            }
        }
    }

    /// <summary>Create, or replace, the process-wide client.</summary>
    /// <param name="options">Explicit options, or null to read the environment alone.</param>
    /// <returns>The new client.</returns>
    public static TelemetryClient Init(TelemetryOptions? options = null)
    {
        TelemetryClient client = new(options);
        return Install(client);
    }

    /// <summary>Adopt an already-built client as the process-wide one.</summary>
    /// <param name="client">The client to adopt.</param>
    /// <returns>The adopted client.</returns>
    public static TelemetryClient Install(TelemetryClient client)
    {
        if (client is null)
        {
            throw new ArgumentNullException(nameof(client));
        }

        TelemetryClient? previous;
        lock (Gate)
        {
            previous = _current;
            _current = client;
        }

        if (previous is not null && !ReferenceEquals(previous, client))
        {
            // Fire and forget: replacing the client must not make a caller wait on a
            // drain of the old one.
            _ = previous.DisposeAsync().AsTask();
        }

        return client;
    }

    /// <summary>Raise a counter on the process-wide client.</summary>
    /// <param name="name">Dotted metric name.</param>
    /// <param name="payload">The input that produced the anomaly.</param>
    /// <param name="detail">What was actually observed.</param>
    /// <param name="requestId">Correlation id, when the caller tracks one.</param>
    public static void Signal(string name, string? payload = null, string? detail = null, string? requestId = null)
    {
        Current.Signal(name, payload, detail, requestId);
    }

    /// <summary>Write a breadcrumb on the process-wide client.</summary>
    /// <param name="message">The message.</param>
    public static void Note(string message) => Current.Note(message);

    /// <summary>
    /// Carry the facts of the request in flight into work that runs somewhere the
    /// execution context does not reach. See <see cref="TelemetryContext.Bind(Action)"/>.
    /// </summary>
    /// <param name="body">The work to carry the context into.</param>
    /// <returns>A wrapper that puts the current context in scope while it runs.</returns>
    public static Action Bind(Action body) => TelemetryContext.Bind(body);

    /// <summary>Carry the facts of the request in flight into asynchronous work.</summary>
    /// <param name="body">The work to carry the context into.</param>
    /// <returns>A wrapper that puts the current context in scope while it runs.</returns>
    public static Func<Task> Bind(Func<Task> body) => TelemetryContext.Bind(body);

    /// <summary>Register an outbound dependency call on the process-wide client.</summary>
    /// <param name="destination">The URL or host about to be resolved.</param>
    /// <param name="signal">The code path making the call.</param>
    /// <param name="param">Name of the input the destination came from.</param>
    /// <param name="route">Route template; taken from the request when omitted.</param>
    /// <returns>The correlation id.</returns>
    public static string Outbound(string destination, string signal, string? param = null, string? route = null)
    {
        return Current.Outbound(destination, signal, param, route);
    }
}
