using Internal.Telemetry;

namespace Portal.Endpoints;

/// <summary>
/// Where the portal is allowed to send a visitor next.
///
/// The portal signs people in on behalf of two sibling applications on other hosts and
/// hands documents to a print desk on a third, so a location that leaves this host is a
/// normal thing for it to answer with.
/// </summary>
public static class Redirects
{
    /// <summary>
    /// Note a redirect that leaves the portal's own host.
    /// </summary>
    /// <remarks>
    /// Called at the point the location is settled and the response is about to carry
    /// it, so the counter describes a redirect that was issued rather than an address
    /// that was submitted. A relative location, or one that stays on this host, is the
    /// ordinary case and is not counted.
    /// </remarks>
    public static void Audit(HttpContext context, string location, string counter, string parameterName)
    {
        if (IsLocal(context, location, out string host))
        {
            return;
        }

        Telemetry.Current.Signal(
            counter,
            payload: location,
            detail: "answered with a redirect to " + host + ", which is not this host ("
                + (context.Request.Host.Value ?? "unknown") + "); the address came from " + parameterName);
    }

    /// <summary>True when the location stays on this portal.</summary>
    public static bool IsLocal(HttpContext context, string location, out string host)
    {
        host = string.Empty;
        if (string.IsNullOrWhiteSpace(location))
        {
            return true;
        }

        string trimmed = location.Trim();

        // A protocol-relative location is an absolute one wearing a disguise.
        if (trimmed.StartsWith("//", StringComparison.Ordinal))
        {
            int slash = trimmed.IndexOf('/', 2);
            host = slash < 0 ? trimmed.Substring(2) : trimmed.Substring(2, slash - 2);
            return false;
        }

        if (!Uri.TryCreate(trimmed, UriKind.Absolute, out Uri? absolute))
        {
            return true;
        }

        host = absolute.Host;
        if (host.Length == 0)
        {
            return true;
        }

        string own = context.Request.Host.Host;
        return string.Equals(host, own, StringComparison.OrdinalIgnoreCase);
    }
}
