using System.Globalization;
using System.Security.Cryptography;
using Internal.Telemetry;
using Portal.Data;

namespace Portal.Security;

/// <summary>The signed-in employee, as the screens need them.</summary>
public sealed class PortalUser
{
    public int Id { get; init; }

    public string Email { get; init; } = string.Empty;

    public string DisplayName { get; init; } = string.Empty;

    public string Nickname { get; init; } = string.Empty;

    public string Telephone { get; init; } = string.Empty;

    public string Site { get; init; } = string.Empty;

    public string CostCentre { get; init; } = string.Empty;

    public decimal ApprovalLimit { get; init; }

    public string DirectoryRole { get; init; } = "member";

    public bool IsAdministrator => string.Equals(DirectoryRole, "administrator", StringComparison.Ordinal);
}

/// <summary>
/// Cookie sessions. The identifier is random and opaque and the record lives in the
/// database; nothing about the holder travels in the cookie itself.
/// </summary>
public sealed class Sessions
{
    public const string CookieName = "PORTALSESSION";

    private readonly Database _database;

    public Sessions(Database database)
    {
        _database = database;
    }

    public async Task<PortalUser?> SignInAsync(HttpContext context, string email, string password)
    {
        List<Dictionary<string, object?>> rows = await _database.QueryAsync(
            "SELECT * FROM employees WHERE lower(email) = lower(@email) AND active",
            ("email", email)).ConfigureAwait(false);
        if (rows.Count != 1)
        {
            return null;
        }

        Dictionary<string, object?> row = rows[0];
        if (!Passwords.Verify(password, Convert.ToString(row["password_hash"], CultureInfo.InvariantCulture) ?? string.Empty))
        {
            return null;
        }

        PortalUser user = Map(row);
        string id = Convert.ToHexString(RandomNumberGenerator.GetBytes(24)).ToLowerInvariant();
        await _database.ExecuteAsync(
            "INSERT INTO sessions (id, employee_id) VALUES (@id, @employee)",
            ("id", id),
            ("employee", user.Id)).ConfigureAwait(false);

        context.Response.Cookies.Append(CookieName, id, new CookieOptions
        {
            HttpOnly = true,
            SameSite = SameSiteMode.Lax,
            Path = "/",
            IsEssential = true,
        });

        Badges.Issue(context, user);
        return user;
    }

    public async Task SignOutAsync(HttpContext context)
    {
        string? id = context.Request.Cookies[CookieName];
        if (!string.IsNullOrEmpty(id))
        {
            await _database.ExecuteAsync("DELETE FROM sessions WHERE id = @id", ("id", id)).ConfigureAwait(false);
        }

        context.Response.Cookies.Delete(CookieName);
        context.Response.Cookies.Delete(Badges.CookieName);
        context.Response.Cookies.Delete("wslayout");
    }

    /// <summary>Resolve the caller once per request and remember it on the request.</summary>
    public async Task<PortalUser?> CurrentAsync(HttpContext context)
    {
        if (context.Items.TryGetValue("portal.user", out object? cached))
        {
            return cached as PortalUser;
        }

        PortalUser? user = null;
        string? id = context.Request.Cookies[CookieName];
        if (!string.IsNullOrEmpty(id))
        {
            List<Dictionary<string, object?>> rows = await _database.QueryAsync(
                "SELECT e.* FROM sessions s JOIN employees e ON e.id = s.employee_id"
                + " WHERE s.id = @id AND e.active",
                ("id", id)).ConfigureAwait(false);
            if (rows.Count == 1)
            {
                user = Map(rows[0]);
            }
        }

        context.Items["portal.user"] = user;
        if (user is not null)
        {
            // The record of this request should say who it was served to.
            Telemetry.Current.SetAuthSubject(user.Id.ToString(CultureInfo.InvariantCulture));
        }

        return user;
    }

    public static PortalUser Map(Dictionary<string, object?> row)
    {
        return new PortalUser
        {
            Id = Convert.ToInt32(row["id"], CultureInfo.InvariantCulture),
            Email = Convert.ToString(row["email"], CultureInfo.InvariantCulture) ?? string.Empty,
            DisplayName = Convert.ToString(row["display_name"], CultureInfo.InvariantCulture) ?? string.Empty,
            Nickname = Convert.ToString(row["nickname"], CultureInfo.InvariantCulture) ?? string.Empty,
            Telephone = Convert.ToString(row["telephone"], CultureInfo.InvariantCulture) ?? string.Empty,
            Site = Convert.ToString(row["site"], CultureInfo.InvariantCulture) ?? string.Empty,
            CostCentre = Convert.ToString(row["cost_centre"], CultureInfo.InvariantCulture) ?? string.Empty,
            ApprovalLimit = Convert.ToDecimal(row["approval_limit"], CultureInfo.InvariantCulture),
            DirectoryRole = Convert.ToString(row["directory_role"], CultureInfo.InvariantCulture) ?? "member",
        };
    }
}
