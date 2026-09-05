using System;
using System.Collections.Generic;
using System.Net;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Primitives;

namespace Internal.Telemetry;

/// <summary>
/// Reads the address of the peer that actually opened the connection.
/// </summary>
/// <remarks>
/// <para>
/// This is the one place in the package where an address becomes evidence rather than
/// description, so it is also the one place where getting it wrong has consequences.
/// The classification of traffic as the estate's own is decided on this address alone;
/// if a caller could influence it, a caller could decide how its own traffic is
/// counted, and could remove itself from every dashboard it appears in.
/// </para>
/// <para>
/// Hence two rules. First: the address comes from the connection and from nowhere else -
/// never <c>X-Forwarded-For</c>, never a helper that folds one in. Second, and less
/// obvious: when the framework's forwarded-headers component runs ahead of this one, it
/// has already replaced the connection address with a value taken from a header, and
/// reading the connection at that point reads the caller's claim wearing the
/// connection's clothes. That case is detected and the peer is reported as unknown,
/// which is honest and, unlike adopting the header, cannot be steered.
/// </para>
/// </remarks>
public static class PeerResolution
{
    /// <summary>
    /// The connection address, or null when it is absent or has already been rewritten
    /// from a header by something earlier in the pipeline.
    /// </summary>
    /// <param name="context">The request being served.</param>
    /// <returns>The socket peer, or null when there is no trustworthy one.</returns>
    public static IPAddress? Peer(HttpContext context)
    {
        if (context is null)
        {
            return null;
        }

        IPAddress? address = context.Connection.RemoteIpAddress;
        if (address is null)
        {
            return null;
        }

        address = SourceMatcher.Fold(address);
        return WasRewritten(context.Request, address) ? null : address;
    }

    /// <summary>
    /// True when the connection address we can see is a caller's claim rather than a
    /// socket address.
    /// </summary>
    /// <remarks>
    /// Two independent detections, because either alone leaves a gap. The framework's
    /// forwarded-headers component removes the entry it consumed from
    /// <c>X-Forwarded-For</c> and files the original connection address under
    /// <c>X-Original-For</c>, so after it has run the address no longer appears in any
    /// forwarded header and only the presence of that second header gives it away. A
    /// third-party component that rewrites the address without leaving that trace is
    /// caught by the other test: the address still appears in a header the caller sent.
    /// </remarks>
    /// <param name="request">The request being served.</param>
    /// <param name="address">The connection address, already folded.</param>
    /// <returns>True when the address must not be treated as a peer.</returns>
    public static bool WasRewritten(HttpRequest request, IPAddress? address)
    {
        if (request is null || address is null)
        {
            return false;
        }

        if (request.Headers.ContainsKey(RequestAttributes.OriginalForHeader))
        {
            return true;
        }

        return MatchesAnnouncedAddress(request.Headers, address);
    }

    /// <summary>
    /// True when the caller itself announced the address we are about to decide on.
    /// </summary>
    /// <param name="headers">The request headers.</param>
    /// <param name="address">The address under consideration.</param>
    /// <returns>True when the address appears in a forwarded header.</returns>
    public static bool MatchesAnnouncedAddress(IHeaderDictionary headers, IPAddress? address)
    {
        if (headers is null || address is null)
        {
            return false;
        }

        string subject = address.ToString();
        foreach (string header in RequestAttributes.ForwardedHeaders)
        {
            StringValues raw = headers[header];
            if (StringValues.IsNullOrEmpty(raw))
            {
                continue;
            }

            foreach (string? line in raw)
            {
                if (line is null)
                {
                    continue;
                }

                foreach (string candidate in EnumerateAnnounced(line))
                {
                    if (string.Equals(candidate, subject, StringComparison.OrdinalIgnoreCase))
                    {
                        return true;
                    }
                }
            }
        }

        return false;
    }

    /// <summary>
    /// The address a human wants to see next to the record: whatever the deployment
    /// calls the client, forwarded values included. Description, never evidence.
    /// </summary>
    /// <param name="context">The request being served.</param>
    /// <returns>A textual address, or null when nothing is known.</returns>
    public static string? ClientIp(HttpContext context)
    {
        if (context is null)
        {
            return null;
        }

        foreach (string header in RequestAttributes.ForwardedHeaders)
        {
            StringValues raw = context.Request.Headers[header];
            if (StringValues.IsNullOrEmpty(raw))
            {
                continue;
            }

            foreach (string? line in raw)
            {
                if (line is null)
                {
                    continue;
                }

                foreach (string candidate in EnumerateAnnounced(line))
                {
                    if (candidate.Length > 0)
                    {
                        return candidate;
                    }
                }
            }
        }

        return SourceMatcher.Format(context.Connection.RemoteIpAddress);
    }

    /// <summary>
    /// Pull the bare addresses out of one forwarded header line, in order.
    /// </summary>
    /// <param name="line">Header value, possibly a list.</param>
    /// <returns>Each announced address, ports and quoting removed.</returns>
    public static IEnumerable<string> EnumerateAnnounced(string line)
    {
        foreach (string chunk in line.Replace(';', ',').Split(','))
        {
            string candidate = chunk.Trim().Trim('"');
            if (candidate.Length == 0)
            {
                continue;
            }

            int equals = candidate.IndexOf('=');
            if (equals >= 0)
            {
                // Forwarded: for=192.0.2.1;proto=https
                candidate = candidate.Substring(equals + 1).Trim().Trim('"');
            }

            IPAddress? parsed = SourceMatcher.Normalise(candidate);
            if (parsed is not null)
            {
                yield return parsed.ToString();
            }
        }
    }
}
