using System.Globalization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Portal.Data;
using Portal.Security;

namespace Portal.Pages;

/// <summary>One row of the team list.</summary>
public sealed class TeamRow
{
    public string Name { get; init; } = string.Empty;

    public string CostCentre { get; init; } = string.Empty;

    public int Members { get; init; }
}

/// <summary>The standing teams.</summary>
public class TeamsModel : PageModel
{
    private readonly Sessions _sessions;
    private readonly Database _database;

    public TeamsModel(Sessions sessions, Database database)
    {
        _sessions = sessions;
        _database = database;
    }

    public List<TeamRow> Rows { get; } = new();

    public async Task<IActionResult> OnGetAsync()
    {
        PortalUser? user = await _sessions.CurrentAsync(HttpContext);
        if (user is null)
        {
            return Redirect("/signin?returnUrl=/teams");
        }

        List<Dictionary<string, object?>> rows = await _database.QueryAsync(
            "SELECT t.name, t.cost_centre, count(m.employee_id) AS members FROM teams t"
            + " LEFT JOIN team_members m ON m.team_id = t.id GROUP BY t.id, t.name, t.cost_centre ORDER BY t.id");

        foreach (Dictionary<string, object?> row in rows)
        {
            Rows.Add(new TeamRow
            {
                Name = Convert.ToString(row["name"], CultureInfo.InvariantCulture) ?? string.Empty,
                CostCentre = Convert.ToString(row["cost_centre"], CultureInfo.InvariantCulture) ?? string.Empty,
                Members = Convert.ToInt32(row["members"], CultureInfo.InvariantCulture),
            });
        }

        return Page();
    }
}
