using System.Globalization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Portal.Data;
using Portal.Security;

namespace Portal.Pages;

/// <summary>
/// Opens a shared document.
///
/// Two answers, because the support desk could not tell "that was never one of our
/// links" from "that link has aged out" when both said the same thing.
/// </summary>
public class ShareModel : PageModel
{
    private readonly Database _database;

    public ShareModel(Database database)
    {
        _database = database;
    }

    public string Heading { get; private set; } = "Shared document";

    public string Explanation { get; private set; } = string.Empty;

    public string Title { get; private set; } = string.Empty;

    public string StoredName { get; private set; } = string.Empty;

    public int DocumentId { get; private set; }

    public string Expires { get; private set; } = string.Empty;

    public async Task<IActionResult> OnGetAsync(string token)
    {
        string peer = HttpContext.Connection.RemoteIpAddress?.ToString() ?? "unknown";
        ShareOutcome outcome = ShareTokens.Read(token, peer, out ShareGrant? grant);

        if (outcome == ShareOutcome.Malformed)
        {
            Response.StatusCode = StatusCodes.Status400BadRequest;
            Heading = "This link is not valid";
            Explanation = "That address is not a share link issued by the portal. Check that the whole "
                + "link was copied, including the part after the last slash.";
            return Page();
        }

        if (outcome == ShareOutcome.Stale || grant is null)
        {
            Response.StatusCode = StatusCodes.Status404NotFound;
            Heading = "This link has expired";
            Explanation = "Share links lapse after a while. Ask whoever sent it to issue a new one from "
                + "the document list.";
            return Page();
        }

        List<Dictionary<string, object?>> rows = await _database.QueryAsync(
            "SELECT id, title, stored_name FROM documents WHERE id = @id", ("id", grant.DocumentId));
        if (rows.Count != 1)
        {
            Response.StatusCode = StatusCodes.Status404NotFound;
            Heading = "This link has expired";
            Explanation = "The document it pointed at is no longer filed here.";
            return Page();
        }

        DocumentId = grant.DocumentId;
        Expires = grant.Expires;
        Title = Convert.ToString(rows[0]["title"], CultureInfo.InvariantCulture) ?? string.Empty;
        StoredName = Convert.ToString(rows[0]["stored_name"], CultureInfo.InvariantCulture) ?? string.Empty;
        Explanation = "Shared with you from the Meridian Castings document store.";
        return Page();
    }
}
