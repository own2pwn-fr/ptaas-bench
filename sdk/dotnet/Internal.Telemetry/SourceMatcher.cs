using System;
using System.Collections.Generic;
using System.Globalization;
using System.Net;
using System.Net.Sockets;

namespace Internal.Telemetry;

/// <summary>
/// Address normalisation and prefix matching for the estate's generated-traffic ranges.
/// </summary>
/// <remarks>
/// Membership of those ranges must never be something a caller can claim for itself, so
/// everything here is fed the socket peer address and nothing else. The helpers are
/// public because the middleware, the client and the tests all have to agree on exactly
/// one reading of an address.
/// </remarks>
public sealed class SourceMatcher
{
    private readonly List<Prefix> _prefixes;

    private SourceMatcher(List<Prefix> prefixes)
    {
        _prefixes = prefixes;
    }

    /// <summary>An empty matcher: nothing is ever inside it.</summary>
    public static SourceMatcher Empty { get; } = new SourceMatcher(new List<Prefix>());

    /// <summary>How many prefixes were understood. Unparseable entries are skipped.</summary>
    public int Count => _prefixes.Count;

    /// <summary>
    /// Compile CIDR prefixes. A bare address is taken as a host route (/32 or /128).
    /// A typo in a deployment variable is skipped rather than thrown: it must not stop a
    /// service from starting.
    /// </summary>
    public static SourceMatcher Compile(IEnumerable<string>? prefixes)
    {
        List<Prefix> compiled = new();
        if (prefixes is null)
        {
            return new SourceMatcher(compiled);
        }

        foreach (string raw in prefixes)
        {
            if (string.IsNullOrWhiteSpace(raw))
            {
                continue;
            }

            string entry = raw.Trim();
            int slash = entry.LastIndexOf('/');
            string addressPart = slash < 0 ? entry : entry.Substring(0, slash);
            IPAddress? address = Normalise(addressPart);
            if (address is null)
            {
                continue;
            }

            byte[] bytes = address.GetAddressBytes();
            int full = bytes.Length * 8;
            int bits = full;
            if (slash >= 0)
            {
                string suffix = entry.Substring(slash + 1).Trim();
                if (!int.TryParse(suffix, NumberStyles.Integer, CultureInfo.InvariantCulture, out bits)
                    || bits < 0
                    || bits > full)
                {
                    continue;
                }
            }

            compiled.Add(new Prefix(bytes, bits));
        }

        return new SourceMatcher(compiled);
    }

    /// <summary>True when the address falls inside one of the compiled prefixes.</summary>
    public bool Matches(IPAddress? address)
    {
        if (address is null || _prefixes.Count == 0)
        {
            return false;
        }

        IPAddress folded = Fold(address);
        byte[] candidate = folded.GetAddressBytes();
        foreach (Prefix prefix in _prefixes)
        {
            if (prefix.Contains(candidate))
            {
                return true;
            }
        }

        return false;
    }

    /// <summary>Textual overload, for callers holding an address they read as a string.</summary>
    public bool Matches(string? address) => Matches(Normalise(address));

    /// <summary>
    /// Read an address as a socket might report it.
    /// </summary>
    /// <remarks>
    /// A dual-stack listener reports IPv4 peers as <c>::ffff:10.0.0.4</c>; comparing that
    /// form against an IPv4 prefix silently never matches, so it is folded back first.
    /// Bracketed literals, a trailing port and an interface suffix are all understood,
    /// because every one of them shows up somewhere in a real deployment.
    /// </remarks>
    public static IPAddress? Normalise(string? address)
    {
        if (string.IsNullOrWhiteSpace(address))
        {
            return null;
        }

        string value = address.Trim();

        // [::1]:8080 and [::1]
        if (value.Length > 1 && value[0] == '[')
        {
            int close = value.IndexOf(']');
            if (close > 1)
            {
                value = value.Substring(1, close - 1);
            }
        }
        else if (value.IndexOf(':') > 0 && value.IndexOf(':') == value.LastIndexOf(':'))
        {
            // Exactly one colon: an IPv4 address with a port, never an IPv6 literal.
            value = value.Substring(0, value.IndexOf(':'));
        }

        int zone = value.IndexOf('%');
        if (zone > 0)
        {
            value = value.Substring(0, zone);
        }

        if (!IPAddress.TryParse(value, out IPAddress? parsed))
        {
            return null;
        }

        return Fold(parsed);
    }

    /// <summary>Fold an IPv4-mapped IPv6 address back to its IPv4 form.</summary>
    public static IPAddress Fold(IPAddress address)
    {
        if (address.AddressFamily != AddressFamily.InterNetworkV6)
        {
            return address;
        }

        if (address.IsIPv4MappedToIPv6)
        {
            return address.MapToIPv4();
        }

        // ScopeId is only readable on an IPv6 address, which is why the family is
        // settled above before it is touched. A scoped address is rebuilt without the
        // interface index: no prefix ever carries one.
        return address.ScopeId != 0 ? new IPAddress(address.GetAddressBytes()) : address;
    }

    /// <summary>Render an address the way it travels on a record.</summary>
    public static string? Format(IPAddress? address)
    {
        return address is null ? null : Fold(address).ToString();
    }

    private readonly struct Prefix
    {
        private readonly byte[] _network;
        private readonly int _bits;

        public Prefix(byte[] network, int bits)
        {
            _network = network;
            _bits = bits;
        }

        public bool Contains(byte[] candidate)
        {
            if (candidate.Length != _network.Length)
            {
                return false;
            }

            int wholeBytes = _bits / 8;
            for (int i = 0; i < wholeBytes; i++)
            {
                if (candidate[i] != _network[i])
                {
                    return false;
                }
            }

            int remainder = _bits % 8;
            if (remainder == 0)
            {
                return true;
            }

            int mask = 0xFF << (8 - remainder);
            return (candidate[wholeBytes] & mask) == (_network[wholeBytes] & mask);
        }
    }
}
