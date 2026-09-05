using System.Collections.Concurrent;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using Internal.Telemetry;

namespace Portal.Security;

/// <summary>How a share token was read.</summary>
public enum ShareOutcome
{
    /// <summary>The token could not be unpadded: it is not a link this portal issued.</summary>
    Malformed,

    /// <summary>The token unpadded cleanly but its check value did not agree.</summary>
    Stale,

    /// <summary>The token is good.</summary>
    Valid,
}

/// <summary>The contents of a share link.</summary>
public sealed class ShareGrant
{
    public int DocumentId { get; init; }

    public int IssuedBy { get; init; }

    public string Expires { get; init; } = string.Empty;
}

/// <summary>
/// Share links.
///
/// A link carries the document it points at, who issued it and when it lapses, chained
/// block encryption over the record and a short check value appended so a link that has
/// been edited in transit is recognised. Product asked for two different messages: one
/// for a link that was never ours, one for a link that has simply aged out, because the
/// support desk could not tell the two apart from the logs.
/// </summary>
public static class ShareTokens
{
    private const int BlockSize = 16;
    private const int CheckLength = 8;

    private static byte[] _key = new byte[32];
    private static byte[] _checkKey = new byte[32];

    // Read-behaviour bookkeeping for the operations dashboard: which final block a caller
    // has been asking about, and which answers it has had for it.
    private static readonly ConcurrentDictionary<string, Probe> Observed = new(StringComparer.Ordinal);
    private static int _raised;

    public static void UseKeys(byte[] key, byte[] checkKey)
    {
        _key = key.Length == 32 ? key : SHA256.HashData(key);
        _checkKey = checkKey.Length == 32 ? checkKey : SHA256.HashData(checkKey);
    }

    /// <summary>Forget what has been observed. Called by the operations reset.</summary>
    public static void Forget()
    {
        Observed.Clear();
        Interlocked.Exchange(ref _raised, 0);
    }

    public static string Issue(int documentId, int issuedBy, string expires)
    {
        string record = "doc=" + documentId.ToString(CultureInfo.InvariantCulture)
            + ";iss=" + issuedBy.ToString(CultureInfo.InvariantCulture)
            + ";exp=" + expires;
        byte[] plaintext = Encoding.ASCII.GetBytes(record);

        byte[] iv = RandomNumberGenerator.GetBytes(BlockSize);
        using Aes aes = Aes.Create();
        aes.Key = _key;
        byte[] ciphertext = aes.EncryptCbc(plaintext, iv, PaddingMode.PKCS7);

        byte[] check = Check(plaintext);
        byte[] token = new byte[iv.Length + ciphertext.Length + CheckLength];
        Buffer.BlockCopy(iv, 0, token, 0, iv.Length);
        Buffer.BlockCopy(ciphertext, 0, token, iv.Length, ciphertext.Length);
        Buffer.BlockCopy(check, 0, token, iv.Length + ciphertext.Length, CheckLength);
        return Badges.Base64Url(token);
    }

    /// <summary>
    /// Read a token. The padding is stripped first and the check value is compared
    /// afterwards, which is the order the two answers below are decided in.
    /// </summary>
    public static ShareOutcome Read(string? token, string peer, out ShareGrant? grant)
    {
        grant = null;
        byte[] raw;
        try
        {
            raw = Badges.FromBase64Url(token ?? string.Empty);
        }
        catch (FormatException)
        {
            return ShareOutcome.Malformed;
        }

        if (raw.Length < BlockSize + BlockSize + CheckLength
            || (raw.Length - CheckLength) % BlockSize != 0)
        {
            return ShareOutcome.Malformed;
        }

        byte[] iv = raw.AsSpan(0, BlockSize).ToArray();
        byte[] ciphertext = raw.AsSpan(BlockSize, raw.Length - BlockSize - CheckLength).ToArray();
        byte[] check = raw.AsSpan(raw.Length - CheckLength, CheckLength).ToArray();

        byte[] plaintext;
        try
        {
            using Aes aes = Aes.Create();
            aes.Key = _key;
            plaintext = aes.DecryptCbc(ciphertext, iv, PaddingMode.PKCS7);
        }
        catch (CryptographicException)
        {
            Observe(peer, ciphertext, false);
            return ShareOutcome.Malformed;
        }

        Observe(peer, ciphertext, true);

        if (!CryptographicOperations.FixedTimeEquals(Check(plaintext), check))
        {
            return ShareOutcome.Stale;
        }

        Dictionary<string, string> fields = new(StringComparer.Ordinal);
        foreach (string chunk in Encoding.ASCII.GetString(plaintext).Split(';'))
        {
            int equals = chunk.IndexOf('=');
            if (equals > 0)
            {
                fields[chunk.Substring(0, equals)] = chunk.Substring(equals + 1);
            }
        }

        if (!fields.TryGetValue("doc", out string? document)
            || !int.TryParse(document, NumberStyles.Integer, CultureInfo.InvariantCulture, out int documentId))
        {
            return ShareOutcome.Stale;
        }

        fields.TryGetValue("iss", out string? issuer);
        fields.TryGetValue("exp", out string? expires);
        grant = new ShareGrant
        {
            DocumentId = documentId,
            IssuedBy = int.TryParse(issuer, NumberStyles.Integer, CultureInfo.InvariantCulture, out int by) ? by : 0,
            Expires = expires ?? string.Empty,
        };
        return ShareOutcome.Valid;
    }

    private static byte[] Check(byte[] plaintext)
    {
        using HMACSHA256 mac = new(_checkKey);
        return mac.ComputeHash(plaintext).AsSpan(0, CheckLength).ToArray();
    }

    /// <summary>
    /// Notice a caller working through one trailing block.
    /// </summary>
    /// <remarks>
    /// The counter here is raised on the effect, not on a suspicious request. One
    /// malformed link is ordinary: people paste half a URL out of an email every day.
    /// What is not ordinary is one caller receiving BOTH answers for links that share a
    /// trailing block and differ in the block in front of it, because at that point the
    /// pair of answers has told the caller something about the record itself. It is
    /// raised once per reset, on the request that completes the pair.
    /// </remarks>
    private static void Observe(string peer, byte[] ciphertext, bool unpadded)
    {
        if (ciphertext.Length < BlockSize * 2)
        {
            return;
        }

        string trailing = Convert.ToHexString(ciphertext.AsSpan(ciphertext.Length - BlockSize, BlockSize));
        string preceding = Convert.ToHexString(
            ciphertext.AsSpan(ciphertext.Length - (BlockSize * 2), BlockSize));

        Probe probe = Observed.GetOrAdd(peer + "|" + trailing, _ => new Probe());
        lock (probe)
        {
            if (unpadded)
            {
                probe.Unpadded.Add(preceding);
            }
            else
            {
                probe.Refused.Add(preceding);
            }

            bool bothAnswers = probe.Unpadded.Count > 0 && probe.Refused.Count > 0;
            HashSet<string> distinct = new(probe.Unpadded, StringComparer.Ordinal);
            distinct.UnionWith(probe.Refused);
            if (!bothAnswers || distinct.Count < 2)
            {
                return;
            }
        }

        if (Interlocked.Exchange(ref _raised, 1) == 0)
        {
            Telemetry.Current.Signal(
                Signals.SharePaddingDistinguished,
                payload: trailing,
                detail: "one caller received both the malformed answer and the stale answer for links"
                    + " sharing the trailing block " + trailing
                    + " while varying the block before it, which settles a byte of the record");
        }
    }

    private sealed class Probe
    {
        public HashSet<string> Unpadded { get; } = new(StringComparer.Ordinal);

        public HashSet<string> Refused { get; } = new(StringComparer.Ordinal);
    }
}
