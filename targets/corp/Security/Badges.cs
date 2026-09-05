using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using Internal.Telemetry;

namespace Portal.Security;

/// <summary>
/// The approval badge.
///
/// It predates the session store: it was a stateless way to carry the approval role to
/// the second-line service, which decrypts the fields it cares about and ignores the
/// rest. Because that service reads fields independently, the record is laid out in
/// fixed sixteen-byte fields and encrypted field by field.
/// </summary>
public static class Badges
{
    public const string CookieName = "badge";

    private const int BlockSize = 16;
    private const int NicknameFieldLength = 32;

    private static byte[] _key = new byte[16];

    public static void UseKey(byte[] key)
    {
        _key = key.Length == 16 ? key : SHA256.HashData(key).AsSpan(0, 16).ToArray();
    }

    /// <summary>The role the directory says this employee holds.</summary>
    public static string RoleOf(PortalUser user) => user.IsAdministrator ? "approver" : "viewer";

    /// <summary>Write the badge cookie for a signed-in employee.</summary>
    public static void Issue(HttpContext context, PortalUser user)
    {
        context.Response.Cookies.Append(CookieName, Encode(user), new CookieOptions
        {
            HttpOnly = false,
            SameSite = SameSiteMode.Lax,
            Path = "/",
            IsEssential = true,
        });
    }

    public static string Encode(PortalUser user)
    {
        string record = Field("uid=" + user.Id.ToString("00000000", CultureInfo.InvariantCulture) + ";")
            + Pad(user.Nickname, NicknameFieldLength)
            + Field("role=" + RoleOf(user) + ";")
            + Field("exp=20271231;");

        byte[] plaintext = Encoding.ASCII.GetBytes(record);
        using Aes aes = Aes.Create();
        aes.Key = _key;
        aes.Mode = CipherMode.ECB;
        aes.Padding = PaddingMode.None;
        byte[] ciphertext = aes.EncryptEcb(plaintext, PaddingMode.None);
        return Base64Url(ciphertext);
    }

    /// <summary>Decode a badge into its fields, or null when it is not a badge at all.</summary>
    public static Dictionary<string, string>? Decode(string? token)
    {
        if (string.IsNullOrEmpty(token))
        {
            return null;
        }

        byte[] ciphertext;
        try
        {
            ciphertext = FromBase64Url(token);
        }
        catch (FormatException)
        {
            return null;
        }

        if (ciphertext.Length == 0 || ciphertext.Length % BlockSize != 0)
        {
            return null;
        }

        byte[] plaintext;
        try
        {
            using Aes aes = Aes.Create();
            aes.Key = _key;
            aes.Mode = CipherMode.ECB;
            aes.Padding = PaddingMode.None;
            plaintext = aes.DecryptEcb(ciphertext, PaddingMode.None);
        }
        catch (CryptographicException)
        {
            return null;
        }

        string record = Encoding.ASCII.GetString(plaintext);
        Dictionary<string, string> fields = new(StringComparer.Ordinal);
        foreach (string chunk in record.Split(';'))
        {
            int equals = chunk.IndexOf('=');
            if (equals < 0)
            {
                continue;
            }

            string key = chunk.Substring(0, equals).Trim();
            if (key.Length == 0)
            {
                continue;
            }

            // The second-line service reads the record left to right and keeps the last
            // assignment it sees, which is how a re-issued field replaces an older one.
            fields[key] = chunk.Substring(equals + 1).Trim();
        }

        return fields.Count == 0 ? null : fields;
    }

    /// <summary>
    /// The role a request is entitled to, and whether that role came from the directory.
    /// </summary>
    /// <remarks>
    /// The counter is raised here, on the effect: a badge that decodes to a role the
    /// directory does not hold for that employee, on a request that was then served with
    /// it. A badge that fails to decode, or one whose role still agrees with the
    /// directory, is ordinary traffic and is not counted.
    /// </remarks>
    public static string Authorise(HttpContext context, PortalUser user, bool served)
    {
        Dictionary<string, string>? fields = Decode(context.Request.Cookies[CookieName]);
        string directoryRole = RoleOf(user);
        if (fields is null || !fields.TryGetValue("role", out string? claimed) || claimed.Length == 0)
        {
            return directoryRole;
        }

        if (!fields.TryGetValue("uid", out string? uid)
            || !int.TryParse(uid, NumberStyles.Integer, CultureInfo.InvariantCulture, out int badgeId)
            || badgeId != user.Id)
        {
            return directoryRole;
        }

        if (string.Equals(claimed, directoryRole, StringComparison.Ordinal))
        {
            return directoryRole;
        }

        if (served)
        {
            Telemetry.Current.Signal(
                Signals.BadgeBlockSplice,
                payload: context.Request.Cookies[CookieName],
                detail: "badge for " + user.Id.ToString(CultureInfo.InvariantCulture) + " presented role '"
                    + claimed + "' where the directory holds '" + directoryRole
                    + "'; the request was served under the presented role");
        }

        return claimed;
    }

    private static string Field(string value) => Pad(value, BlockSize);

    private static string Pad(string value, int width)
    {
        string ascii = new(value.Where(c => c >= 0x20 && c < 0x7F).ToArray());
        return ascii.Length >= width ? ascii.Substring(0, width) : ascii.PadRight(width, ' ');
    }

    public static string Base64Url(byte[] value)
    {
        return Convert.ToBase64String(value).TrimEnd('=').Replace('+', '-').Replace('/', '_');
    }

    public static byte[] FromBase64Url(string value)
    {
        string padded = value.Replace('-', '+').Replace('_', '/');
        switch (padded.Length % 4)
        {
            case 2:
                padded += "==";
                break;
            case 3:
                padded += "=";
                break;
        }

        return Convert.FromBase64String(padded);
    }
}
