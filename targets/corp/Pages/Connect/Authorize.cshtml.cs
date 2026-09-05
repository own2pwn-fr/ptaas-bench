using System.Globalization;
using System.Security.Cryptography;
using Internal.Telemetry;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Portal.Data;
using Portal.Security;

namespace Portal.Pages.Connect;

/// <summary>
/// Hands an authorisation code to a connected application.
///
/// The equipment desk runs on several sub-paths of the portal host and the shift board
/// on its own host, so the return address is matched against the origin each application
/// registered rather than against each individual path, which the desk team were
/// re-registering weekly.
/// </summary>
public class AuthorizeModel : PageModel
{
    private readonly Sessions _sessions;
    private readonly Database _database;

    public AuthorizeModel(Sessions sessions, Database database)
    {
        _sessions = sessions;
        _database = database;
    }

    public string Message { get; private set; } = string.Empty;

    public async Task<IActionResult> OnGetAsync(
        string? client_id,
        string? redirect_uri,
        string? response_type,
        string? state)
    {
        PortalUser? user = await _sessions.CurrentAsync(HttpContext);
        if (user is null)
        {
            return Redirect("/signin?returnUrl=/connect/authorize");
        }

        string clientId = (client_id ?? string.Empty).Trim();
        string redirect = (redirect_uri ?? string.Empty).Trim();

        List<Dictionary<string, object?>> rows = await _database.QueryAsync(
            "SELECT id, name, redirect_uris FROM oauth_clients WHERE id = @id", ("id", clientId));
        if (rows.Count != 1)
        {
            Message = "That application is not registered with the portal.";
            return Page();
        }

        string registered = Convert.ToString(rows[0]["redirect_uris"], CultureInfo.InvariantCulture)
            ?? string.Empty;
        string[] uris = registered.Split(' ', StringSplitOptions.RemoveEmptyEntries);
        if (redirect.Length == 0)
        {
            redirect = uris.Length > 0 ? uris[0] : string.Empty;
        }

        if (!Accepted(uris, redirect))
        {
            Message = "That return address is not registered for "
                + (Convert.ToString(rows[0]["name"], CultureInfo.InvariantCulture) ?? clientId) + ".";
            return Page();
        }

        if (!string.Equals(response_type, "code", StringComparison.Ordinal))
        {
            Message = "Only the authorisation code flow is supported.";
            return Page();
        }

        string code = Convert.ToHexString(RandomNumberGenerator.GetBytes(16)).ToLowerInvariant();
        await _database.ExecuteAsync(
            "INSERT INTO oauth_codes (code, client_id, employee_id, redirect_uri, issued_at)"
            + " VALUES (@c, @cl, @e, @r, now())",
            ("c", code), ("cl", clientId), ("e", user.Id), ("r", redirect));

        string location = redirect + (redirect.Contains('?', StringComparison.Ordinal) ? "&" : "?")
            + "code=" + code
            + (string.IsNullOrEmpty(state) ? string.Empty : "&state=" + Uri.EscapeDataString(state));

        AuditDelivery(uris, redirect, clientId, user, code);
        return Redirect(location);
    }

    /// <summary>The registered origin is the unit of comparison, not the whole address.</summary>
    private static bool Accepted(string[] registered, string candidate)
    {
        if (candidate.Length == 0)
        {
            return false;
        }

        foreach (string uri in registered)
        {
            if (!Uri.TryCreate(uri, UriKind.Absolute, out Uri? parsed))
            {
                continue;
            }

            string origin = parsed.Scheme + "://" + parsed.Host;
            if (candidate.StartsWith(origin, StringComparison.OrdinalIgnoreCase))
            {
                return true;
            }
        }

        return false;
    }

    /// <summary>
    /// Note a code that was handed to a host nobody registered.
    /// </summary>
    /// <remarks>
    /// Raised after the code exists and the response is about to carry it, and only when
    /// the host it is being delivered to is not one of the hosts registered for that
    /// application. A refused request never gets here, and a delivery to a registered
    /// host is the ordinary case.
    /// </remarks>
    private static void AuditDelivery(
        string[] registered,
        string redirect,
        string clientId,
        PortalUser user,
        string code)
    {
        if (!Uri.TryCreate(redirect, UriKind.Absolute, out Uri? delivered))
        {
            return;
        }

        foreach (string uri in registered)
        {
            if (Uri.TryCreate(uri, UriKind.Absolute, out Uri? parsed)
                && string.Equals(parsed.Host, delivered.Host, StringComparison.OrdinalIgnoreCase))
            {
                return;
            }
        }

        Telemetry.Current.Signal(
            Signals.AuthorizeForeignDelivery,
            payload: redirect,
            detail: "an authorisation code for " + clientId + " and employee "
                + user.Id.ToString(CultureInfo.InvariantCulture) + " was delivered to " + delivered.Host
                + ", which is not a registered host for that application (code " + code.Substring(0, 8) + ")");
    }
}
