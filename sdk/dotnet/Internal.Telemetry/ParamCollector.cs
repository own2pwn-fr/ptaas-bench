using System;
using System.Collections.Generic;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace Internal.Telemetry;

/// <summary>
/// Ordered, de-duplicated, bounded accumulator of described inputs.
/// </summary>
/// <remarks>
/// <para>
/// De-duplication is on (location, name, value digest) and NOT on (location, name). A
/// repeated parameter carrying a different value is the interesting case: it is what
/// makes two identical-looking requests behave differently, and collapsing a repeated
/// name to a single entry would hide exactly the requests worth looking at. Identical
/// repeats do collapse, because they carry no extra information.
/// </para>
/// <para>
/// Bounded because a pathological document must not be able to make one record cost
/// more than the request that produced it.
/// </para>
/// </remarks>
public sealed class ParamCollector
{
    /// <summary>The collector clips a sample at this length; so does this.</summary>
    public const int SampleMaxChars = 256;

    private readonly List<ParamEntry> _entries = new();
    private readonly HashSet<string> _seen = new(StringComparer.Ordinal);
    private readonly int _max;

    /// <summary>Create a collector holding at most <paramref name="maxEntries"/> entries.</summary>
    /// <param name="maxEntries">Upper bound on recorded attributes.</param>
    public ParamCollector(int maxEntries)
    {
        _max = maxEntries < 1 ? 1 : maxEntries;
    }

    /// <summary>True once the bound was hit and something was left out.</summary>
    public bool Truncated { get; private set; }

    /// <summary>The described inputs, in the order they were observed.</summary>
    public List<ParamEntry> Entries => _entries;

    /// <summary>Describe one textual input.</summary>
    /// <param name="name">Input name.</param>
    /// <param name="location">Where it arrived from: query, json, body, path, header, cookie, multipart, raw.</param>
    /// <param name="value">The raw value as it arrived.</param>
    public void Add(string name, string location, string? value)
    {
        AddEntry(Describe(name, location, value ?? string.Empty));
    }

    /// <summary>Describe one binary input, hashing and sizing the bytes themselves.</summary>
    /// <param name="name">Input name.</param>
    /// <param name="location">Where it arrived from.</param>
    /// <param name="value">The raw bytes as they arrived.</param>
    public void AddBytes(string name, string location, byte[] value)
    {
        AddEntry(Describe(name, location, value));
    }

    /// <summary>Merge entries described elsewhere, honouring the same de-duplication.</summary>
    /// <param name="entries">Entries to merge.</param>
    public void AddRange(IEnumerable<ParamEntry>? entries)
    {
        if (entries is null)
        {
            return;
        }

        foreach (ParamEntry entry in entries)
        {
            AddEntry(entry);
        }
    }

    private void AddEntry(ParamEntry entry)
    {
        if (_entries.Count >= _max)
        {
            Truncated = true;
            return;
        }

        string key = entry.In + " " + entry.Name + " " + (entry.ValueSha256 ?? string.Empty);
        if (!_seen.Add(key))
        {
            return;
        }

        _entries.Add(entry);
    }

    /// <summary>Build one described input from text.</summary>
    /// <param name="name">Input name.</param>
    /// <param name="location">Where it arrived from.</param>
    /// <param name="value">The raw value.</param>
    /// <returns>The described input.</returns>
    public static ParamEntry Describe(string name, string location, string value)
    {
        string text = value ?? string.Empty;
        byte[] bytes = Encoding.UTF8.GetBytes(text);
        return new ParamEntry
        {
            Name = name,
            In = location,
            ValueSha256 = Sha256Hex(bytes),
            ValueLength = bytes.Length,
            Sample = Truncate(text),
        };
    }

    /// <summary>Build one described input from bytes.</summary>
    /// <param name="name">Input name.</param>
    /// <param name="location">Where it arrived from.</param>
    /// <param name="value">The raw bytes.</param>
    /// <returns>The described input.</returns>
    public static ParamEntry Describe(string name, string location, byte[] value)
    {
        byte[] bytes = value ?? Array.Empty<byte>();
        return new ParamEntry
        {
            Name = name,
            In = location,
            ValueSha256 = Sha256Hex(bytes),
            ValueLength = bytes.Length,
            Sample = Truncate(Encoding.UTF8.GetString(bytes)),
        };
    }

    /// <summary>Hex sha256 of the UTF-8 encoding of a string.</summary>
    /// <param name="value">Value to digest.</param>
    /// <returns>Lower-case hex digest.</returns>
    public static string Sha256Hex(string value)
    {
        return Sha256Hex(Encoding.UTF8.GetBytes(value ?? string.Empty));
    }

    /// <summary>Hex sha256 of a byte sequence.</summary>
    /// <param name="value">Bytes to digest.</param>
    /// <returns>Lower-case hex digest.</returns>
    public static string Sha256Hex(byte[] value)
    {
        byte[] digest = SHA256.HashData(value ?? Array.Empty<byte>());
        return Convert.ToHexString(digest).ToLowerInvariant();
    }

    /// <summary>
    /// Clip a sample without leaving a broken surrogate pair behind: a lone high
    /// surrogate serialises to something some JSON readers reject outright.
    /// </summary>
    /// <param name="raw">Value to clip.</param>
    /// <returns>At most <see cref="SampleMaxChars"/> characters.</returns>
    public static string Truncate(string raw)
    {
        if (string.IsNullOrEmpty(raw))
        {
            return string.Empty;
        }

        if (raw.Length <= SampleMaxChars)
        {
            return raw;
        }

        string cut = raw.Substring(0, SampleMaxChars);
        return char.IsHighSurrogate(cut[cut.Length - 1]) ? cut.Substring(0, cut.Length - 1) : cut;
    }

    /// <summary>
    /// Render a JSON leaf the way it looked on the wire, so that the same value carried
    /// as JSON, as a form field or as a query parameter produces one digest.
    /// </summary>
    /// <param name="element">The leaf.</param>
    /// <returns>Its textual form.</returns>
    public static string JsonLeafText(JsonElement element)
    {
        switch (element.ValueKind)
        {
            case JsonValueKind.String:
                return element.GetString() ?? string.Empty;
            case JsonValueKind.True:
                return "true";
            case JsonValueKind.False:
                return "false";
            case JsonValueKind.Null:
            case JsonValueKind.Undefined:
                return "null";
            default:
                return element.GetRawText();
        }
    }

    /// <summary>
    /// Flatten a decoded document into one entry per leaf, under dotted paths such as
    /// shipping.address.city or items.0.sku.
    /// </summary>
    /// <remarks>
    /// Dashboards address a single field by name, so a nested payload has to be
    /// reachable under the flat name a caller would use to talk about it. Empty
    /// containers are recorded as leaves, so "the client sent this key" stays visible
    /// even when it sent nothing inside it.
    /// </remarks>
    /// <param name="collector">Destination.</param>
    /// <param name="element">Document or fragment.</param>
    /// <param name="location">Location label for every leaf.</param>
    /// <param name="prefix">Path accumulated so far.</param>
    /// <param name="maxDepth">Nesting bound.</param>
    /// <param name="depth">Current depth.</param>
    public static void Flatten(
        ParamCollector collector,
        JsonElement element,
        string location,
        string prefix,
        int maxDepth,
        int depth = 0)
    {
        if (collector is null)
        {
            return;
        }

        if (depth < maxDepth && element.ValueKind == JsonValueKind.Object)
        {
            bool any = false;
            foreach (JsonProperty property in element.EnumerateObject())
            {
                any = true;
                string path = prefix.Length == 0 ? property.Name : prefix + "." + property.Name;
                Flatten(collector, property.Value, location, path, maxDepth, depth + 1);
            }

            if (!any)
            {
                collector.Add(prefix.Length == 0 ? "body" : prefix, location, "{}");
            }

            return;
        }

        if (depth < maxDepth && element.ValueKind == JsonValueKind.Array)
        {
            int index = 0;
            foreach (JsonElement item in element.EnumerateArray())
            {
                string path = prefix.Length == 0
                    ? index.ToString(CultureInfo.InvariantCulture)
                    : prefix + "." + index.ToString(CultureInfo.InvariantCulture);
                Flatten(collector, item, location, path, maxDepth, depth + 1);
                index++;
            }

            if (index == 0)
            {
                collector.Add(prefix.Length == 0 ? "body" : prefix, location, "[]");
            }

            return;
        }

        collector.Add(prefix.Length == 0 ? "body" : prefix, location, JsonLeafText(element));
    }
}
