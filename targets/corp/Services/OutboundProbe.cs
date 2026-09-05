using System.Globalization;
using System.Net;
using System.Net.Http;
using System.Net.Sockets;
using System.Text;
using Internal.Telemetry;

namespace Portal.Services;

/// <summary>What one outbound check saw.</summary>
public sealed class ProbeResult
{
    public bool Completed { get; init; }

    public int Status { get; init; }

    public string ContentType { get; init; } = string.Empty;

    public string Body { get; init; } = string.Empty;

    public string RemoteAddress { get; init; } = string.Empty;

    public string Error { get; init; } = string.Empty;
}

/// <summary>
/// Checks an endpoint from the portal itself.
///
/// The integrations screen exists so that the back office can tell "the partner is down"
/// from "the partner is down for us", which is only answerable from inside this network,
/// so the check is made here rather than in the browser. Each check gets its own client
/// so that the address it connected to is unambiguous in the result: a pooled connection
/// would report the address of whatever opened it, which is exactly the detail the
/// screen exists to show.
/// </summary>
public sealed class OutboundProbe
{
    private const int BodyLimit = 4096;

    public async Task<ProbeResult> FetchAsync(
        string url,
        string counter,
        string parameterName,
        CancellationToken token)
    {
        IPEndPoint? observed = null;

        SocketsHttpHandler handler = new()
        {
            AllowAutoRedirect = false,
            ConnectTimeout = TimeSpan.FromSeconds(4),
            ConnectCallback = async (context, cancellation) =>
            {
                Socket socket = new(SocketType.Stream, ProtocolType.Tcp) { NoDelay = true };
                try
                {
                    await socket.ConnectAsync(context.DnsEndPoint, cancellation).ConfigureAwait(false);
                    observed = socket.RemoteEndPoint as IPEndPoint;
                    return new NetworkStream(socket, ownsSocket: true);
                }
                catch (Exception)
                {
                    socket.Dispose();
                    throw;
                }
            },
        };

        using HttpClient client = new(handler, disposeHandler: true)
        {
            Timeout = TimeSpan.FromSeconds(6),
        };
        client.DefaultRequestHeaders.TryAddWithoutValidation("User-Agent", "MeridianPortal/4.2 (integration check)");

        // Declared before the call so the egress this causes can be tied back to the
        // request that asked for it; the name lookup follows within microseconds.
        Telemetry.Current.Outbound(url, counter, parameterName);

        try
        {
            using HttpResponseMessage response = await client
                .GetAsync(url, HttpCompletionOption.ResponseHeadersRead, token)
                .ConfigureAwait(false);

            byte[] buffer = new byte[BodyLimit];
            int read = 0;
            await using (Stream stream = await response.Content.ReadAsStreamAsync(token).ConfigureAwait(false))
            {
                while (read < BodyLimit)
                {
                    int got = await stream.ReadAsync(buffer.AsMemory(read, BodyLimit - read), token)
                        .ConfigureAwait(false);
                    if (got <= 0)
                    {
                        break;
                    }

                    read += got;
                }
            }

            ProbeResult result = new()
            {
                Completed = true,
                Status = (int)response.StatusCode,
                ContentType = response.Content.Headers.ContentType?.ToString() ?? string.Empty,
                Body = Encoding.UTF8.GetString(buffer, 0, read),
                RemoteAddress = observed?.Address.ToString() ?? string.Empty,
            };

            AuditDestination(url, parameterName, observed, result);
            return result;
        }
        catch (Exception error)
        {
            return new ProbeResult
            {
                Completed = false,
                RemoteAddress = observed?.Address.ToString() ?? string.Empty,
                Error = error.GetType().Name,
            };
        }
    }

    /// <summary>
    /// Note a check that reached the host's own configuration service.
    /// </summary>
    /// <remarks>
    /// Raised on the address the socket actually connected to and only when an answer
    /// came back, which is the difference between a check that was attempted and a check
    /// that succeeded. The address is read from the connected socket rather than from
    /// the URL, so a name that resolves to the link-local range is caught the same way a
    /// literal is.
    /// </remarks>
    private static void AuditDestination(string url, string parameterName, IPEndPoint? observed, ProbeResult result)
    {
        if (observed is null || !IsLinkLocal(observed.Address))
        {
            return;
        }

        Telemetry.Current.Signal(
            Signals.ProbeLinkLocal,
            payload: url,
            detail: "the check connected to " + observed.Address + " and was answered with status "
                + result.Status.ToString(CultureInfo.InvariantCulture) + " and "
                + result.Body.Length.ToString(CultureInfo.InvariantCulture) + " bytes; that address carries"
                + " the host's own instance configuration and is not a partner endpoint (parameter "
                + parameterName + ")");
    }

    public static bool IsLinkLocal(IPAddress address)
    {
        // A socket opened without an address family is dual stack, so an IPv4 peer comes
        // back in its mapped form and has to be unwrapped before the range is decided.
        // Without this the check reads ::ffff:169.254.169.254 as an ordinary IPv6
        // address and says no.
        if (address.IsIPv4MappedToIPv6)
        {
            address = address.MapToIPv4();
        }

        if (address.AddressFamily == AddressFamily.InterNetwork)
        {
            byte[] bytes = address.GetAddressBytes();
            return bytes[0] == 169 && bytes[1] == 254;
        }

        return address.IsIPv6LinkLocal;
    }
}
