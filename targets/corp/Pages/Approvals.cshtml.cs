using System.Globalization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Portal.Data;
using Portal.Security;

namespace Portal.Pages;

/// <summary>One row of the approvals list.</summary>
public sealed class ApprovalRow
{
    public string Reference { get; init; } = string.Empty;

    public string Amount { get; init; } = string.Empty;

    public string State { get; init; } = string.Empty;
}

/// <summary>The requests the signed-in employee raised.</summary>
public class ApprovalsModel : PageModel
{
    private readonly Sessions _sessions;
    private readonly Database _database;

    public ApprovalsModel(Sessions sessions, Database database)
    {
        _sessions = sessions;
        _database = database;
    }

    public List<ApprovalRow> Rows { get; } = new();

    public async Task<IActionResult> OnGetAsync()
    {
        PortalUser? user = await _sessions.CurrentAsync(HttpContext);
        if (user is null)
        {
            return Redirect("/signin?returnUrl=/approvals");
        }

        List<Dictionary<string, object?>> rows = await _database.QueryAsync(
            "SELECT reference, amount, state FROM approvals WHERE requested_by = @id ORDER BY id",
            ("id", user.Id));

        foreach (Dictionary<string, object?> row in rows)
        {
            Rows.Add(new ApprovalRow
            {
                Reference = Convert.ToString(row["reference"], CultureInfo.InvariantCulture) ?? string.Empty,
                Amount = Convert.ToDecimal(row["amount"], CultureInfo.InvariantCulture)
                    .ToString("N0", CultureInfo.InvariantCulture),
                State = Convert.ToString(row["state"], CultureInfo.InvariantCulture) ?? string.Empty,
            });
        }

        return Page();
    }
}
