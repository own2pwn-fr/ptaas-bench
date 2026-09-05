using System.Globalization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Portal.Data;
using Portal.Security;

namespace Portal.Pages.Documents;

/// <summary>One row of the document list.</summary>
public sealed class DocumentRow
{
    public int Id { get; init; }

    public string Title { get; init; } = string.Empty;

    public string StoredName { get; init; } = string.Empty;

    public string CostCentre { get; init; } = string.Empty;

    public string Created { get; init; } = string.Empty;

    public string ShareToken { get; init; } = string.Empty;
}

/// <summary>The document list, and the form that files a new one.</summary>
public class IndexModel : PageModel
{
    private readonly Sessions _sessions;
    private readonly Database _database;

    public IndexModel(Sessions sessions, Database database)
    {
        _sessions = sessions;
        _database = database;
    }

    public List<DocumentRow> Rows { get; } = new();

    public async Task<IActionResult> OnGetAsync()
    {
        PortalUser? user = await _sessions.CurrentAsync(HttpContext);
        if (user is null)
        {
            return Redirect("/signin?returnUrl=/documents");
        }

        List<Dictionary<string, object?>> rows = await _database.QueryAsync(
            "SELECT id, title, stored_name, cost_centre, created_at FROM documents ORDER BY id DESC LIMIT 40");

        foreach (Dictionary<string, object?> row in rows)
        {
            int id = Convert.ToInt32(row["id"], CultureInfo.InvariantCulture);
            Rows.Add(new DocumentRow
            {
                Id = id,
                Title = Convert.ToString(row["title"], CultureInfo.InvariantCulture) ?? string.Empty,
                StoredName = Convert.ToString(row["stored_name"], CultureInfo.InvariantCulture) ?? string.Empty,
                CostCentre = Convert.ToString(row["cost_centre"], CultureInfo.InvariantCulture) ?? string.Empty,
                Created = Convert.ToDateTime(row["created_at"], CultureInfo.InvariantCulture)
                    .ToString("d MMM yyyy", CultureInfo.InvariantCulture),
                ShareToken = ShareTokens.Issue(id, user.Id, "20271231"),
            });
        }

        return Page();
    }
}
