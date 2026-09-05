using System;
using System.IO;
using System.Net;
using System.Security.Claims;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;

namespace Internal.Telemetry;

/// <summary>Knobs for <see cref="TelemetryMiddleware"/>.</summary>
public sealed class TelemetryMiddlewareOptions
{
    /// <summary>Skip a request entirely: liveness probes, asset floods.</summary>
    public Func<HttpContext, bool>? Ignore { get; set; }

    /// <summary>
    /// Resolve the authenticated principal. The default reads the usual claims, which
    /// is what a per-tenant dashboard groups by.
    /// </summary>
    public Func<HttpContext, string?>? Identify { get; set; }
}

/// <summary>
/// Records exactly one request record per request, once the response has been written.
/// </summary>
/// <remarks>
/// <para>
/// Register it early, ahead of routing and of anything that reads the body: the body has
/// to be made re-readable before the framework binds a model out of it. The route is
/// still reported correctly, because the template is read after the pipeline has run,
/// from the endpoint routing selected on this same context.
/// </para>
/// <para>
/// Nothing here is visible to a client. No response header is set, no route is added, no
/// log line is written on the happy path, no error body is touched. Instrumentation that
/// changes what a client sees is instrumentation that changes what it measures, and
/// several things downstream are measured in milliseconds.
/// </para>
/// </remarks>
public sealed class TelemetryMiddleware
{
    private readonly RequestDelegate _next;
    private readonly TelemetryClient _client;
    private readonly TelemetryMiddlewareOptions _options;

    /// <summary>Build the middleware.</summary>
    /// <param name="next">The rest of the pipeline.</param>
    /// <param name="client">The client to record to.</param>
    /// <param name="options">Knobs.</param>
    public TelemetryMiddleware(RequestDelegate next, TelemetryClient client, TelemetryMiddlewareOptions options)
    {
        _next = next ?? throw new ArgumentNullException(nameof(next));
        _client = client ?? throw new ArgumentNullException(nameof(client));
        _options = options ?? new TelemetryMiddlewareOptions();
    }

    /// <summary>Serve one request and record it.</summary>
    /// <param name="context">The request.</param>
    /// <returns>A task completing when the pipeline has run.</returns>
    public async Task InvokeAsync(HttpContext context)
    {
        bool active;
        try
        {
            active = _client.Enabled && !(_options.Ignore is not null && _options.Ignore(context));
        }
        catch (Exception)
        {
            active = false;
        }

        if (!active)
        {
            await _next(context).ConfigureAwait(false);
            return;
        }

        RequestContext requestContext;
        byte[] body = Array.Empty<byte>();
        double started = Transport.UnixSeconds();

        try
        {
            IPAddress? peer = PeerResolution.Peer(context);
            requestContext = new RequestContext
            {
                RequestId = Guid.NewGuid().ToString("n"),
                PeerAddress = peer,
                ClientIp = PeerResolution.ClientIp(context),

                // Decided on the connection address alone. Anything else would let a
                // caller choose how its own traffic is counted.
                Synthetic = _client.IsSyntheticPeer(peer),
            };

            TelemetryContext.Set(requestContext);
            context.Items[TelemetryContext.ItemsKey] = requestContext;

            if (_client.Config.MaxBodyBytes > 0 && RequestAttributes.DeclaresBody(context.Request))
            {
                // Re-readable for the application: the bytes are buffered here and the
                // stream is rewound, so model binding sees exactly what arrived.
                context.Request.EnableBuffering();
                body = await ReadBoundedAsync(
                    context.Request.Body,
                    _client.Config.MaxBodyBytes,
                    context.RequestAborted).ConfigureAwait(false);
                if (context.Request.Body.CanSeek)
                {
                    context.Request.Body.Position = 0;
                }
            }
        }
        catch (Exception)
        {
            // Anything unexpected in the setup above and the request is served
            // uninstrumented. Falling through is the only acceptable failure mode.
            TelemetryContext.Clear();
            await _next(context).ConfigureAwait(false);
            return;
        }

        try
        {
            await _next(context).ConfigureAwait(false);
        }
        finally
        {
            try
            {
                Record(context, requestContext, body, started);
            }
            catch (Exception)
            {
                // Never propagate, never log. A stack trace on standard output would
                // put this package's noise into the application's own logs.
            }

            TelemetryContext.Clear();
        }
    }

    /// <summary>
    /// The route template as routing registered it, never a concrete URL.
    /// </summary>
    /// <remarks>
    /// Read from the endpoint the routing middleware selected on this context. Razor
    /// Pages registers its patterns without a leading slash while the minimal routing
    /// APIs keep one, so the result is normalised: a dashboard grouping by template
    /// cannot have the same endpoint under two names.
    /// </remarks>
    /// <param name="context">The request.</param>
    /// <returns>The template, or the unmatched marker.</returns>
    public static string RouteTemplate(HttpContext context)
    {
        try
        {
            if (context.GetEndpoint() is RouteEndpoint endpoint)
            {
                string? raw = endpoint.RoutePattern.RawText;
                if (!string.IsNullOrWhiteSpace(raw))
                {
                    string template = raw.Trim();
                    return template[0] == '/' ? template : "/" + template;
                }
            }
        }
        catch (Exception)
        {
            // A custom endpoint that objects to being read is not worth a failed request.
        }

        return TelemetryRoute.Unmatched;
    }

    private void Record(HttpContext context, RequestContext ambient, byte[] body, double started)
    {
        if (!_client.Enabled)
        {
            return;
        }

        string template = RouteTemplate(context);
        ambient.Route ??= template;

        ParamCollector collector = _client.NewParamCollector();
        RequestAttributes.CollectQuery(collector, context.Request);
        RequestAttributes.CollectRouteValues(collector, context.Request);
        RequestAttributes.CollectHeaders(collector, context.Request);
        RequestAttributes.CollectCookies(collector, context.Request);
        if (body.Length > 0)
        {
            RequestAttributes.CollectBody(
                collector,
                body,
                context.Request.ContentType,
                _client.Config.MaxBodyDepth);
        }

        collector.AddRange(ambient.ExtraParams);

        HttpRequestEvent record = new()
        {
            App = _client.Service,
            Timestamp = started,
            Method = context.Request.Method.ToUpperInvariant(),
            Route = template,
            Path = context.Request.Path.HasValue ? context.Request.Path.Value : "/",
            Status = context.Response.StatusCode,
            AuthSubject = ambient.AuthSubject ?? Identify(context),
            UserAgent = context.Request.Headers.UserAgent.ToString(),
            Params = collector.Entries,
            PeerIp = ambient.PeerIp,
            ClientIp = ambient.ClientIp,
        };

        if (ambient.Synthetic)
        {
            record.Synthetic = true;
        }

        _client.Emit(record);
    }

    private string? Identify(HttpContext context)
    {
        try
        {
            if (_options.Identify is not null)
            {
                return _options.Identify(context);
            }

            ClaimsPrincipal? user = context.User;
            if (user?.Identity is null || !user.Identity.IsAuthenticated)
            {
                return null;
            }

            Claim? claim = user.FindFirst(ClaimTypes.NameIdentifier) ?? user.FindFirst("sub");
            return claim?.Value ?? user.Identity.Name;
        }
        catch (Exception)
        {
            return null;
        }
    }

    /// <summary>
    /// Read at most <paramref name="max"/> bytes, leaving anything beyond it for the
    /// application to stream. A large upload is therefore neither held in memory nor
    /// delayed on its way to the handler.
    /// </summary>
    private static async Task<byte[]> ReadBoundedAsync(Stream stream, int max, CancellationToken token)
    {
        byte[] buffer = new byte[Math.Min(max, 16 * 1024)];
        using MemoryStream sink = new();
        int total = 0;
        while (total < max)
        {
            int want = Math.Min(buffer.Length, max - total);
            int read = await stream.ReadAsync(buffer.AsMemory(0, want), token).ConfigureAwait(false);
            if (read <= 0)
            {
                break;
            }

            sink.Write(buffer, 0, read);
            total += read;
        }

        return sink.ToArray();
    }
}
