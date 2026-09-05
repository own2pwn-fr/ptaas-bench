using System;
using System.Collections.Generic;
using System.Net;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Primitives;

namespace Internal.Telemetry;

/// <summary>
/// Turns a served request into the described inputs a record carries.
/// </summary>
/// <remarks>
/// Every location a handler could have read from is described: the query string, the
/// body in whichever shape it arrived, the route values routing bound, the cookies and
/// the headers a handler may key behaviour off. The body is read from the raw bytes
/// rather than from the framework's bound model, because the bound model only shows what
/// the handler asked for, and the requests worth looking at are the ones where the
/// handler asked for the wrong thing.
/// </remarks>
public static class RequestAttributes
{
    /// <summary>
    /// Headers worth describing: the ones a handler, a proxy or a cache may key
    /// behaviour off. Everything beginning with <c>x-</c> is described too, because
    /// per-tenant and feature-toggle routing lives in custom headers.
    /// </summary>
    public static readonly IReadOnlyList<string> DescribedHeaders = new[]
    {
        "host",
        "referer",
        "referrer",
        "user-agent",
        "origin",
        "content-type",
        "accept-language",
        "authorization",
        "forwarded",
        "true-client-ip",
    };

    /// <summary>
    /// Headers through which a caller can announce an address about itself. They are
    /// described as ordinary inputs, and they never take part in a decision.
    /// </summary>
    public static readonly IReadOnlyList<string> ForwardedHeaders = new[]
    {
        "x-forwarded-for",
        "x-real-ip",
        "forwarded",
        "true-client-ip",
        "client-ip",
    };

    /// <summary>
    /// Header the framework's forwarded-headers component writes when it has already
    /// replaced the connection address with a value taken from a header.
    /// </summary>
    public const string OriginalForHeader = "X-Original-For";

    private static readonly HashSet<string> DescribedSet =
        new(DescribedHeaders, StringComparer.OrdinalIgnoreCase);

    private static readonly Regex PartName =
        new("name=\"((?:[^\"\\\\]|\\\\.)*)\"", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);

    private static readonly Regex PartFileName =
        new("filename=\"((?:[^\"\\\\]|\\\\.)*)\"", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);

    /// <summary>True when the request announces a body worth buffering.</summary>
    /// <param name="request">The request.</param>
    /// <returns>True when a body is declared.</returns>
    public static bool DeclaresBody(HttpRequest request)
    {
        if (request is null)
        {
            return false;
        }

        string? transfer = request.Headers["Transfer-Encoding"].ToString();
        if (!string.IsNullOrEmpty(transfer) && transfer.IndexOf("chunked", StringComparison.OrdinalIgnoreCase) >= 0)
        {
            return true;
        }

        return request.ContentLength.GetValueOrDefault() > 0;
    }

    /// <summary>Describe the query string, one entry per repeated value.</summary>
    /// <param name="collector">Destination.</param>
    /// <param name="request">The request.</param>
    public static void CollectQuery(ParamCollector collector, HttpRequest request)
    {
        foreach (KeyValuePair<string, StringValues> pair in request.Query)
        {
            foreach (string? value in pair.Value)
            {
                collector.Add(pair.Key, "query", value ?? string.Empty);
            }
        }
    }

    /// <summary>Describe the values routing bound out of the path.</summary>
    /// <param name="collector">Destination.</param>
    /// <param name="request">The request.</param>
    public static void CollectRouteValues(ParamCollector collector, HttpRequest request)
    {
        foreach (KeyValuePair<string, object?> pair in request.RouteValues)
        {
            collector.Add(pair.Key, "path", pair.Value?.ToString() ?? string.Empty);
        }
    }

    /// <summary>Describe the cookies, parsed from the raw header.</summary>
    /// <remarks>
    /// Parsed by hand rather than read from the framework's cookie collection: a cookie
    /// the application never reads is still part of what the client sent, and a
    /// malformed one is usually the reason the request is being looked at at all.
    /// </remarks>
    /// <param name="collector">Destination.</param>
    /// <param name="request">The request.</param>
    public static void CollectCookies(ParamCollector collector, HttpRequest request)
    {
        foreach (string? raw in request.Headers["Cookie"])
        {
            if (string.IsNullOrEmpty(raw))
            {
                continue;
            }

            foreach (string chunk in raw.Split(';'))
            {
                string piece = chunk.Trim();
                if (piece.Length == 0)
                {
                    continue;
                }

                int equals = piece.IndexOf('=');
                string name = equals < 0 ? piece : piece.Substring(0, equals).Trim();
                if (name.Length == 0)
                {
                    continue;
                }

                string value = equals < 0 ? string.Empty : piece.Substring(equals + 1).Trim();
                if (value.Length >= 2 && value[0] == '"' && value[value.Length - 1] == '"')
                {
                    value = value.Substring(1, value.Length - 2);
                }

                collector.Add(name, "cookie", Unescape(value));
            }
        }
    }

    /// <summary>Describe the headers worth describing.</summary>
    /// <param name="collector">Destination.</param>
    /// <param name="request">The request.</param>
    public static void CollectHeaders(ParamCollector collector, HttpRequest request)
    {
        foreach (KeyValuePair<string, StringValues> header in request.Headers)
        {
            string lowered = header.Key.ToLowerInvariant();
            if (lowered == "cookie")
            {
                continue;
            }

            if (!DescribedSet.Contains(lowered) && !lowered.StartsWith("x-", StringComparison.Ordinal))
            {
                continue;
            }

            collector.Add(lowered, "header", header.Value.ToString());
        }
    }

    /// <summary>
    /// Describe a body by content type, sniffing JSON when no type was declared.
    /// </summary>
    /// <param name="collector">Destination.</param>
    /// <param name="body">The buffered bytes.</param>
    /// <param name="contentType">The declared content type, possibly empty.</param>
    /// <param name="maxDepth">Nesting bound for a structured document.</param>
    public static void CollectBody(ParamCollector collector, byte[] body, string? contentType, int maxDepth)
    {
        if (body is null || body.Length == 0)
        {
            return;
        }

        string type = (contentType ?? string.Empty);
        int semicolon = type.IndexOf(';');
        string basic = (semicolon < 0 ? type : type.Substring(0, semicolon)).Trim().ToLowerInvariant();

        bool looksLikeJson = basic.Length == 0 && (body[0] == (byte)'{' || body[0] == (byte)'[');
        if (basic == "application/json" || basic.EndsWith("+json", StringComparison.Ordinal) || looksLikeJson)
        {
            try
            {
                using JsonDocument document = JsonDocument.Parse(body);
                ParamCollector.Flatten(collector, document.RootElement, "json", string.Empty, maxDepth);
                return;
            }
            catch (JsonException)
            {
                collector.AddBytes("body", "raw", body);
                return;
            }
        }

        if (basic == "application/x-www-form-urlencoded")
        {
            foreach (KeyValuePair<string, string> pair in ParseUrlEncoded(Encoding.UTF8.GetString(body)))
            {
                collector.Add(pair.Key, "body", pair.Value);
            }

            return;
        }

        if (basic.StartsWith("multipart/", StringComparison.Ordinal))
        {
            bool found = false;
            foreach (MultipartField field in ParseMultipart(body, type))
            {
                found = true;
                if (field.FileName is not null)
                {
                    collector.Add(field.Name + ".filename", "multipart", field.FileName);
                }

                collector.AddBytes(field.Name, "multipart", field.Value);
            }

            if (!found)
            {
                collector.AddBytes("body", "raw", body);
            }

            return;
        }

        if (basic == "application/xml" || basic == "text/xml" || basic.EndsWith("+xml", StringComparison.Ordinal))
        {
            collector.AddBytes("body", "raw", body);
            return;
        }

        collector.AddBytes("body", "raw", body);
    }

    /// <summary>Split an urlencoded payload into decoded pairs, blanks kept.</summary>
    /// <param name="text">The payload.</param>
    /// <returns>One pair per field occurrence, repeats included.</returns>
    public static List<KeyValuePair<string, string>> ParseUrlEncoded(string text)
    {
        List<KeyValuePair<string, string>> pairs = new();
        if (string.IsNullOrEmpty(text))
        {
            return pairs;
        }

        foreach (string chunk in text.Split('&'))
        {
            if (chunk.Length == 0)
            {
                continue;
            }

            int equals = chunk.IndexOf('=');
            string rawName = equals < 0 ? chunk : chunk.Substring(0, equals);
            string rawValue = equals < 0 ? string.Empty : chunk.Substring(equals + 1);
            string name = Unescape(rawName.Replace('+', ' '));
            if (name.Length == 0)
            {
                continue;
            }

            pairs.Add(new KeyValuePair<string, string>(name, Unescape(rawValue.Replace('+', ' '))));
        }

        return pairs;
    }

    /// <summary>One part of a multipart body.</summary>
    public readonly struct MultipartField
    {
        /// <summary>Create a described part.</summary>
        /// <param name="name">Field name.</param>
        /// <param name="fileName">Client-supplied file name, when the part had one.</param>
        /// <param name="value">Raw part bytes.</param>
        public MultipartField(string name, string? fileName, byte[] value)
        {
            Name = name;
            FileName = fileName;
            Value = value;
        }

        /// <summary>Field name from the part's disposition.</summary>
        public string Name { get; }

        /// <summary>Client-supplied file name, or null for an ordinary field.</summary>
        public string? FileName { get; }

        /// <summary>Raw part bytes.</summary>
        public byte[] Value { get; }
    }

    /// <summary>
    /// Split a multipart body by hand.
    /// </summary>
    /// <remarks>
    /// Written by hand rather than with the framework's reader because this one has to
    /// survive the malformed bodies that turn up in production - missing terminator,
    /// truncated upload, bogus part headers - without raising: an exception here would
    /// cost the whole record, and a malformed upload is exactly the request someone will
    /// want to look at. Latin-1 is used for the structural pass because it maps bytes to
    /// characters one for one, so an index found in the text is an index in the bytes.
    /// </remarks>
    /// <param name="body">The buffered bytes.</param>
    /// <param name="contentType">The full content type, carrying the boundary.</param>
    /// <returns>One entry per part that declared a name.</returns>
    public static List<MultipartField> ParseMultipart(byte[] body, string contentType)
    {
        List<MultipartField> fields = new();
        string? boundary = BoundaryOf(contentType);
        if (boundary is null || body is null || body.Length == 0)
        {
            return fields;
        }

        string text = Encoding.Latin1.GetString(body);
        string delimiter = "--" + boundary;
        int cursor = text.IndexOf(delimiter, StringComparison.Ordinal);
        if (cursor < 0)
        {
            return fields;
        }

        while (cursor >= 0)
        {
            int start = cursor + delimiter.Length;
            int next = text.IndexOf(delimiter, start, StringComparison.Ordinal);
            int end = next < 0 ? text.Length : next;
            string part = text.Substring(start, end - start);
            cursor = next;

            if (part.StartsWith("--", StringComparison.Ordinal))
            {
                break;
            }

            int split = part.IndexOf("\r\n\r\n", StringComparison.Ordinal);
            if (split < 0)
            {
                continue;
            }

            string head = part.Substring(0, split);
            Match nameMatch = PartName.Match(head);
            if (!nameMatch.Success)
            {
                continue;
            }

            string payload = part.Substring(split + 4);
            if (payload.EndsWith("\r\n", StringComparison.Ordinal))
            {
                payload = payload.Substring(0, payload.Length - 2);
            }

            Match fileMatch = PartFileName.Match(head);
            fields.Add(new MultipartField(
                nameMatch.Groups[1].Value,
                fileMatch.Success ? fileMatch.Groups[1].Value : null,
                Encoding.Latin1.GetBytes(payload)));
        }

        return fields;
    }

    private static string? BoundaryOf(string contentType)
    {
        if (string.IsNullOrEmpty(contentType))
        {
            return null;
        }

        string[] chunks = contentType.Split(';');
        for (int i = 1; i < chunks.Length; i++)
        {
            string chunk = chunks[i].Trim();
            int equals = chunk.IndexOf('=');
            if (equals < 0)
            {
                continue;
            }

            if (!string.Equals(chunk.Substring(0, equals).Trim(), "boundary", StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }

            string value = chunk.Substring(equals + 1).Trim().Trim('"');
            return value.Length == 0 ? null : value;
        }

        return null;
    }

    private static string Unescape(string value)
    {
        try
        {
            return WebUtility.UrlDecode(value);
        }
        catch (Exception)
        {
            // Broken percent escapes are precisely the case worth seeing on a
            // dashboard; keep the raw characters instead of losing the observation.
            return value;
        }
    }
}
