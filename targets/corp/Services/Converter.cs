using System.Globalization;
using System.Net;
using System.Net.Sockets;
using System.Text;
using Internal.Telemetry;

namespace Portal.Services;

/// <summary>
/// The document converter daemon.
///
/// It is the oldest thing in this image: a line-oriented service that turns a stored
/// document into print output. Its dialect looks like HTTP and is not, which is why the
/// portal writes to it directly rather than through an HTTP client - two attempts to use
/// one ended in the client rewriting the request into something the daemon rejected.
///
/// It listens on the loopback address only. Nothing outside the container speaks to it.
/// </summary>
public sealed class ConverterDaemon : BackgroundService
{
    private readonly int _port;

    public ConverterDaemon(int port)
    {
        _port = port;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        TcpListener listener = new(IPAddress.Loopback, _port);
        listener.Start();
        try
        {
            while (!stoppingToken.IsCancellationRequested)
            {
                TcpClient client;
                try
                {
                    client = await listener.AcceptTcpClientAsync(stoppingToken).ConfigureAwait(false);
                }
                catch (OperationCanceledException)
                {
                    break;
                }

                _ = Task.Run(() => ServeAsync(client, stoppingToken), CancellationToken.None);
            }
        }
        finally
        {
            listener.Stop();
        }
    }

    private static async Task ServeAsync(TcpClient client, CancellationToken token)
    {
        using (client)
        {
            try
            {
                client.NoDelay = true;
                NetworkStream stream = client.GetStream();
                string head = await ReadHeadAsync(stream, token).ConfigureAwait(false);
                if (head.Length == 0)
                {
                    return;
                }

                string[] lines = head.Split("\r\n", StringSplitOptions.None);
                List<string> accepted = new();
                string profile = string.Empty;
                string document = string.Empty;

                for (int i = 1; i < lines.Length; i++)
                {
                    string line = lines[i];
                    int colon = line.IndexOf(':');
                    if (colon <= 0)
                    {
                        continue;
                    }

                    string field = line.Substring(0, colon).Trim();
                    string value = line.Substring(colon + 1).Trim();
                    if (field.Length == 0)
                    {
                        continue;
                    }

                    accepted.Add(field);
                    if (string.Equals(field, "X-Render-Profile", StringComparison.OrdinalIgnoreCase))
                    {
                        profile = value;
                    }
                    else if (string.Equals(field, "X-Render-Document", StringComparison.OrdinalIgnoreCase))
                    {
                        document = value;
                    }
                }

                string job = Guid.NewGuid().ToString("n").Substring(0, 8);
                StringBuilder response = new();
                response.Append("SPOOL/1.0 200 OK\r\n");
                response.Append("X-Spool-Job: ").Append(job).Append("\r\n");
                response.Append("X-Spool-Document: ").Append(Sanitise(document)).Append("\r\n");
                response.Append("X-Spool-Profile: ").Append(Sanitise(profile)).Append("\r\n");
                response.Append("X-Spool-Accepted: ").Append(string.Join(",", accepted.Select(Sanitise)))
                    .Append("\r\n");
                response.Append("X-Spool-Pages: ")
                    .Append(((accepted.Count % 4) + 1).ToString(CultureInfo.InvariantCulture)).Append("\r\n");
                response.Append("\r\n");

                byte[] bytes = Encoding.ASCII.GetBytes(response.ToString());
                await stream.WriteAsync(bytes, token).ConfigureAwait(false);
                await stream.FlushAsync(token).ConfigureAwait(false);
            }
            catch (Exception)
            {
                // A malformed job costs the caller its render, never the daemon.
            }
        }
    }

    private static async Task<string> ReadHeadAsync(NetworkStream stream, CancellationToken token)
    {
        byte[] buffer = new byte[8192];
        int total = 0;
        while (total < buffer.Length)
        {
            int read = await stream.ReadAsync(buffer.AsMemory(total, buffer.Length - total), token)
                .ConfigureAwait(false);
            if (read <= 0)
            {
                break;
            }

            total += read;
            string text = Encoding.ASCII.GetString(buffer, 0, total);
            int end = text.IndexOf("\r\n\r\n", StringComparison.Ordinal);
            if (end >= 0)
            {
                return text.Substring(0, end);
            }
        }

        return Encoding.ASCII.GetString(buffer, 0, total);
    }

    private static string Sanitise(string value)
    {
        return new string(value.Where(c => c >= 0x20 && c < 0x7F).ToArray());
    }
}

/// <summary>The reply the daemon gave for one render.</summary>
public sealed class RenderReply
{
    public bool Completed { get; init; }

    public string Status { get; init; } = string.Empty;

    public Dictionary<string, string> Fields { get; init; } = new(StringComparer.OrdinalIgnoreCase);
}

/// <summary>
/// Speaks to the converter.
///
/// The request is assembled here because the daemon's dialect is not quite HTTP: it
/// wants its own version token on the first line and it refuses the header ordering an
/// HTTP client produces.
/// </summary>
public sealed class ConverterClient
{
    /// <summary>The fields this client writes. Anything else in a reply came from elsewhere.</summary>
    public static readonly string[] EmittedFields = { "X-Render-Profile", "X-Render-User", "X-Render-Document" };

    private readonly int _port;

    public ConverterClient(int port)
    {
        _port = port;
    }

    public async Task<RenderReply> RenderAsync(int documentId, string profile, int employeeId, CancellationToken token)
    {
        string request = "RENDER /doc/" + documentId.ToString(CultureInfo.InvariantCulture) + " SPOOL/1.0\r\n"
            + "X-Render-Profile: " + profile + "\r\n"
            + "X-Render-User: " + employeeId.ToString(CultureInfo.InvariantCulture) + "\r\n"
            + "X-Render-Document: " + documentId.ToString(CultureInfo.InvariantCulture) + "\r\n"
            + "\r\n";

        RenderReply reply;
        try
        {
            using TcpClient client = new();
            await client.ConnectAsync(IPAddress.Loopback, _port, token).ConfigureAwait(false);
            NetworkStream stream = client.GetStream();
            byte[] bytes = Encoding.ASCII.GetBytes(request);
            await stream.WriteAsync(bytes, token).ConfigureAwait(false);
            await stream.FlushAsync(token).ConfigureAwait(false);
            reply = Parse(await ReadAllAsync(stream, token).ConfigureAwait(false));
        }
        catch (Exception)
        {
            return new RenderReply { Completed = false };
        }

        Audit(profile, reply);
        return reply;
    }

    /// <summary>
    /// Note a render whose job carried a field this client does not write.
    /// </summary>
    /// <remarks>
    /// The daemon reports the field names it parsed, so this is a statement about what
    /// arrived rather than about what was sent: the counter is raised when the daemon
    /// read a field the client never emitted, which means the value of one field became
    /// structure on the way across. A profile carrying odd characters that arrive inside
    /// the field they were written into does not raise it.
    /// </remarks>
    private static void Audit(string profile, RenderReply reply)
    {
        if (!reply.Completed || !reply.Fields.TryGetValue("X-Spool-Accepted", out string? accepted))
        {
            return;
        }

        List<string> unexpected = new();
        foreach (string field in accepted.Split(',', StringSplitOptions.RemoveEmptyEntries))
        {
            string name = field.Trim();
            if (name.Length == 0)
            {
                continue;
            }

            bool known = false;
            foreach (string emitted in EmittedFields)
            {
                if (string.Equals(name, emitted, StringComparison.OrdinalIgnoreCase))
                {
                    known = true;
                    break;
                }
            }

            if (!known)
            {
                unexpected.Add(name);
            }
        }

        if (unexpected.Count == 0)
        {
            return;
        }

        Telemetry.Current.Signal(
            Signals.RenderFieldInjected,
            payload: profile,
            detail: "the converter parsed " + string.Join(", ", unexpected)
                + ", which this client never writes; the job's header block gained structure from a value");
    }

    private static async Task<string> ReadAllAsync(NetworkStream stream, CancellationToken token)
    {
        byte[] buffer = new byte[8192];
        int total = 0;
        while (total < buffer.Length)
        {
            int read = await stream.ReadAsync(buffer.AsMemory(total, buffer.Length - total), token)
                .ConfigureAwait(false);
            if (read <= 0)
            {
                break;
            }

            total += read;
            string text = Encoding.ASCII.GetString(buffer, 0, total);
            if (text.Contains("\r\n\r\n", StringComparison.Ordinal))
            {
                break;
            }
        }

        return Encoding.ASCII.GetString(buffer, 0, total);
    }

    private static RenderReply Parse(string text)
    {
        if (text.Length == 0)
        {
            return new RenderReply { Completed = false };
        }

        string[] lines = text.Split("\r\n", StringSplitOptions.None);
        Dictionary<string, string> fields = new(StringComparer.OrdinalIgnoreCase);
        for (int i = 1; i < lines.Length; i++)
        {
            int colon = lines[i].IndexOf(':');
            if (colon > 0)
            {
                fields[lines[i].Substring(0, colon).Trim()] = lines[i].Substring(colon + 1).Trim();
            }
        }

        return new RenderReply { Completed = true, Status = lines[0], Fields = fields };
    }
}
