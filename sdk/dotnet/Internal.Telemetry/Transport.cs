using System;
using System.Collections.Generic;
using System.Globalization;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading;
using System.Threading.Channels;
using System.Threading.Tasks;

namespace Internal.Telemetry;

/// <summary>A snapshot of what the exporter has done so far.</summary>
public sealed class TransportStats
{
    /// <summary>Records waiting in memory.</summary>
    public int Queued { get; init; }

    /// <summary>Records accepted into the queue since start.</summary>
    public long Enqueued { get; init; }

    /// <summary>Records discarded because the queue was full.</summary>
    public long Dropped { get; init; }

    /// <summary>Records the collector accepted.</summary>
    public long Sent { get; init; }

    /// <summary>Records lost to an unreachable or failing collector.</summary>
    public long Failed { get; init; }

    /// <summary>Batches posted, successfully or not.</summary>
    public long Batches { get; init; }

    /// <summary>Dependency links posted.</summary>
    public long LinksSent { get; init; }

    /// <summary>Dependency links that could not be posted.</summary>
    public long LinksFailed { get; init; }
}

/// <summary>
/// The exporter: a bounded in-memory queue drained by one background loop.
/// </summary>
/// <remarks>
/// <para>
/// Everything here serves one rule: the collector must never be observable from a served
/// request. A client that adds latency, or that adds latency only when the collector is
/// slow, corrupts the very numbers it exists to measure, and turns a collector outage
/// into an application outage.
/// </para>
/// <para>
/// Consequences, all intended. Recording is a non-blocking write to a bounded channel -
/// no I/O, no name resolution, no lock held across a syscall, and no exception allowed
/// to escape into the caller. The channel drops the OLDEST record when it is full and
/// counts the drop: during an incident the most recent records are the ones somebody is
/// looking at, and back-pressure is never applied to the application, because a lost
/// record costs a line on a dashboard and a blocked request costs a user. A collector
/// that is down, hung or answering 500s only ever moves counters.
/// </para>
/// </remarks>
public sealed class Transport : IAsyncDisposable
{
    private static readonly JsonSerializerOptions Json = new()
    {
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
    };

    private readonly TelemetryConfig _config;
    private readonly Channel<object> _channel;
    private readonly CancellationTokenSource _stopping = new();
    private readonly object _gate = new();

    private Task? _loop;
    private HttpClient? _http;
    private HttpClient? _linkHttp;
    private int _queued;
    private int _inFlight;
    private int _unreportedDrops;
    private long _enqueued;
    private long _dropped;
    private long _sent;
    private long _failed;
    private long _batches;
    private long _linksSent;
    private long _linksFailed;
    private bool _closed;

    /// <summary>Build an exporter for a resolved configuration.</summary>
    /// <param name="config">The configuration to export under.</param>
    public Transport(TelemetryConfig config)
    {
        _config = config ?? throw new ArgumentNullException(nameof(config));
        BoundedChannelOptions options = new(Math.Max(1, config.MaxQueueSize))
        {
            FullMode = BoundedChannelFullMode.DropOldest,
            SingleReader = true,
            SingleWriter = false,
            AllowSynchronousContinuations = false,
        };
        _channel = Channel.CreateBounded<object>(options, OnDropped);
    }

    /// <summary>Current counters.</summary>
    /// <returns>A snapshot.</returns>
    public TransportStats Stats()
    {
        return new TransportStats
        {
            Queued = Volatile.Read(ref _queued),
            Enqueued = Interlocked.Read(ref _enqueued),
            Dropped = Interlocked.Read(ref _dropped),
            Sent = Interlocked.Read(ref _sent),
            Failed = Interlocked.Read(ref _failed),
            Batches = Interlocked.Read(ref _batches),
            LinksSent = Interlocked.Read(ref _linksSent),
            LinksFailed = Interlocked.Read(ref _linksFailed),
        };
    }

    /// <summary>
    /// Queue a record. Returns immediately, whatever the state of the collector.
    /// </summary>
    /// <param name="record">The record to export.</param>
    public void Enqueue(object record)
    {
        if (record is null || _closed)
        {
            return;
        }

        try
        {
            EnsureLoop();
            Interlocked.Increment(ref _queued);
            if (_channel.Writer.TryWrite(record))
            {
                Interlocked.Increment(ref _enqueued);
                return;
            }

            // A bounded drop-oldest channel accepts unconditionally while it is open,
            // so this is only reached once the writer has been completed.
            Interlocked.Decrement(ref _queued);
        }
        catch (Exception)
        {
            // The one acceptable failure mode is a missing data point.
            Interlocked.Decrement(ref _queued);
        }
    }

    /// <summary>
    /// Post a dependency link now, on its own connection, instead of queueing it.
    /// </summary>
    /// <remarks>
    /// The name lookup this link explains follows within microseconds, so anything that
    /// waited for the next export would arrive after the effect it describes. Immediate
    /// still means off the request path: the send is started and never awaited here.
    /// </remarks>
    /// <param name="link">The link to post.</param>
    public void DispatchLink(OutboundLink link)
    {
        if (link is null || _closed || _config.Endpoint is null)
        {
            return;
        }

        try
        {
            _ = SendLinkAsync(link);
        }
        catch (Exception)
        {
            Interlocked.Increment(ref _linksFailed);
        }
    }

    /// <summary>
    /// Wait until the queue has drained. For shutdown hooks and tests; no request path
    /// ever awaits this.
    /// </summary>
    /// <param name="timeout">How long to wait before giving up.</param>
    /// <returns>True when everything queued has been handed to the collector.</returns>
    public async Task<bool> FlushAsync(TimeSpan timeout)
    {
        EnsureLoop();
        DateTime deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            if (Volatile.Read(ref _queued) == 0 && Volatile.Read(ref _inFlight) == 0)
            {
                return true;
            }

            await Task.Delay(5).ConfigureAwait(false);
        }

        return Volatile.Read(ref _queued) == 0 && Volatile.Read(ref _inFlight) == 0;
    }

    /// <summary>Stop the exporter and drain what is left. Safe to call more than once.</summary>
    /// <returns>A task completing when the loop has stopped.</returns>
    public async ValueTask DisposeAsync()
    {
        if (_closed)
        {
            return;
        }

        _closed = true;
        try
        {
            _channel.Writer.TryComplete();
            Task? loop = _loop;
            if (loop is not null)
            {
                await Task.WhenAny(loop, Task.Delay(TimeSpan.FromSeconds(2))).ConfigureAwait(false);
            }
        }
        catch (Exception)
        {
            // Shutdown is best effort by construction.
        }
        finally
        {
            _stopping.Cancel();
            _http?.Dispose();
            _linkHttp?.Dispose();
            _stopping.Dispose();
        }
    }

    private void OnDropped(object record)
    {
        Interlocked.Decrement(ref _queued);
        Interlocked.Increment(ref _dropped);
        Interlocked.Increment(ref _unreportedDrops);
    }

    private void EnsureLoop()
    {
        if (_loop is not null)
        {
            return;
        }

        lock (_gate)
        {
            _loop ??= Task.Run(RunAsync);
        }
    }

    private async Task RunAsync()
    {
        ChannelReader<object> reader = _channel.Reader;
        try
        {
            while (await reader.WaitToReadAsync(_stopping.Token).ConfigureAwait(false))
            {
                List<object> batch = new();
                int room = _config.BatchSize;
                if (Volatile.Read(ref _unreportedDrops) > 0)
                {
                    room -= 1;
                }

                while (batch.Count < room && reader.TryRead(out object record))
                {
                    Interlocked.Decrement(ref _queued);
                    batch.Add(record);
                }

                AppendDropNote(batch);
                if (batch.Count == 0)
                {
                    continue;
                }

                Interlocked.Increment(ref _inFlight);
                try
                {
                    await PostAsync(batch).ConfigureAwait(false);
                }
                finally
                {
                    Interlocked.Decrement(ref _inFlight);
                }
            }
        }
        catch (OperationCanceledException)
        {
            // Ordinary shutdown.
        }
        catch (ChannelClosedException)
        {
            // Ordinary shutdown.
        }
        catch (Exception)
        {
            // The exporter must not be able to bring down its host process.
        }
    }

    private void AppendDropNote(List<object> batch)
    {
        int dropped = Interlocked.Exchange(ref _unreportedDrops, 0);
        if (dropped <= 0)
        {
            return;
        }

        if (!_config.ReportDiscards)
        {
            return;
        }

        batch.Add(new NoteEvent
        {
            App = _config.Service,
            Timestamp = UnixSeconds(),
            Synthetic = true,
            Message = "telemetry: discarded " + dropped.ToString(CultureInfo.InvariantCulture)
                + " record(s), queue limit reached",
        });
    }

    private async Task PostAsync(List<object> batch)
    {
        Interlocked.Increment(ref _batches);
        if (_config.Endpoint is null)
        {
            Interlocked.Add(ref _failed, batch.Count);
            return;
        }

        string body;
        try
        {
            body = JsonSerializer.Serialize(new EventBatch { Events = batch }, Json);
        }
        catch (Exception)
        {
            body = SerialiseOneByOne(batch);
        }

        bool ok = await SendAsync(Client(), _config.EventsPath, body).ConfigureAwait(false);
        if (ok)
        {
            Interlocked.Add(ref _sent, batch.Count);
        }
        else
        {
            Interlocked.Add(ref _failed, batch.Count);
        }
    }

    /// <summary>
    /// Serialise a batch that failed as a whole, sacrificing only the records that
    /// cannot be written. Letting one bad value fail the whole post would silently
    /// delete unrelated records queued alongside it.
    /// </summary>
    private static string SerialiseOneByOne(List<object> batch)
    {
        List<object> good = new(batch.Count);
        foreach (object record in batch)
        {
            try
            {
                JsonSerializer.Serialize(record, Json);
                good.Add(record);
            }
            catch (Exception)
            {
                // Left out on purpose.
            }
        }

        try
        {
            return JsonSerializer.Serialize(new EventBatch { Events = good }, Json);
        }
        catch (Exception)
        {
            return "{\"events\":[]}";
        }
    }

    private async Task SendLinkAsync(OutboundLink link)
    {
        string body;
        try
        {
            body = JsonSerializer.Serialize(link, Json);
        }
        catch (Exception)
        {
            Interlocked.Increment(ref _linksFailed);
            return;
        }

        bool ok = await SendAsync(LinkClient(), _config.CorrelationsPath, body).ConfigureAwait(false);
        if (ok)
        {
            Interlocked.Increment(ref _linksSent);
        }
        else
        {
            Interlocked.Increment(ref _linksFailed);
        }
    }

    private async Task<bool> SendAsync(HttpClient client, string path, string body)
    {
        try
        {
            using StringContent content = new(body, Encoding.UTF8, "application/json");
            using HttpResponseMessage response =
                await client.PostAsync(path, content, _stopping.Token).ConfigureAwait(false);
            return (int)response.StatusCode < 500;
        }
        catch (Exception)
        {
            // Collector down, name gone, timed out: a no-op by design. Nothing is
            // requeued, because a retry storm against a dead collector would compete
            // with the service for CPU.
            return false;
        }
    }

    private HttpClient Client()
    {
        if (_http is null)
        {
            lock (_gate)
            {
                _http ??= BuildClient();
            }
        }

        return _http;
    }

    private HttpClient LinkClient()
    {
        if (_linkHttp is null)
        {
            lock (_gate)
            {
                // A connection pool of its own: a link must not queue behind a large
                // export that is already on the wire.
                _linkHttp ??= BuildClient();
            }
        }

        return _linkHttp;
    }

    private HttpClient BuildClient()
    {
        HttpClient client = _config.MessageHandler is null
            ? new HttpClient()
            : new HttpClient(_config.MessageHandler, disposeHandler: false);
        client.BaseAddress = new Uri((_config.Endpoint ?? "http://localhost") + "/");
        client.Timeout = _config.RequestTimeout;
        return client;
    }

    /// <summary>Unix epoch seconds with fractions, the way every record carries time.</summary>
    /// <returns>Seconds since the epoch.</returns>
    public static double UnixSeconds()
    {
        return DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0;
    }
}
