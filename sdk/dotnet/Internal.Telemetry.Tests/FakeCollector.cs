using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Net;
using System.Net.Http;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace Internal.Telemetry.Tests;

/// <summary>
/// Stands in for the collector: captures every posted body and answers 202, the way the
/// real ingest endpoint does.
/// </summary>
public sealed class FakeCollector : HttpMessageHandler
{
    private readonly ConcurrentQueue<(string Path, string Body)> _posts = new();

    /// <summary>Wait this long before answering, to model a slow collector.</summary>
    public TimeSpan Delay { get; set; } = TimeSpan.Zero;

    /// <summary>Answer with this status instead of 202.</summary>
    public HttpStatusCode Status { get; set; } = HttpStatusCode.Accepted;

    /// <summary>Throw instead of answering, to model an unreachable collector.</summary>
    public bool Unreachable { get; set; }

    /// <summary>Everything posted so far, in order.</summary>
    public IReadOnlyCollection<(string Path, string Body)> Posts => _posts;

    /// <summary>Bodies posted to the record ingest path.</summary>
    /// <returns>Each batch, parsed.</returns>
    public List<JsonDocument> Batches()
    {
        List<JsonDocument> parsed = new();
        foreach ((string path, string body) in _posts)
        {
            if (path.EndsWith("/v1/traces", StringComparison.Ordinal) || path.EndsWith("/v1/events", StringComparison.Ordinal))
            {
                parsed.Add(JsonDocument.Parse(body));
            }
        }

        return parsed;
    }

    /// <summary>Every record of a given kind across every batch.</summary>
    /// <param name="type">The record kind.</param>
    /// <returns>The matching records.</returns>
    public List<JsonElement> Records(string type)
    {
        List<JsonElement> found = new();
        foreach (JsonDocument document in Batches())
        {
            foreach (JsonElement record in document.RootElement.GetProperty("events").EnumerateArray())
            {
                if (record.TryGetProperty("type", out JsonElement kind) && kind.GetString() == type)
                {
                    found.Add(record.Clone());
                }
            }
        }

        return found;
    }

    /// <summary>Every dependency link posted.</summary>
    /// <returns>The links, parsed.</returns>
    public List<JsonElement> Links()
    {
        List<JsonElement> found = new();
        foreach ((string path, string body) in _posts)
        {
            if (path.EndsWith("/v1/correlations", StringComparison.Ordinal))
            {
                found.Add(JsonDocument.Parse(body).RootElement.Clone());
            }
        }

        return found;
    }

    /// <inheritdoc />
    protected override async Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage request,
        CancellationToken cancellationToken)
    {
        string body = request.Content is null
            ? string.Empty
            : await request.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
        _posts.Enqueue((request.RequestUri?.AbsolutePath ?? string.Empty, body));

        if (Delay > TimeSpan.Zero)
        {
            await Task.Delay(Delay, cancellationToken).ConfigureAwait(false);
        }

        if (Unreachable)
        {
            throw new HttpRequestException("no route to collector");
        }

        return new HttpResponseMessage(Status);
    }
}
