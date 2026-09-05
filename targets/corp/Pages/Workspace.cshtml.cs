using System.Globalization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Portal.Data;
using Portal.Security;
using Portal.Services;

namespace Portal.Pages;

/// <summary>
/// The workspace.
///
/// The arrangement is cached in a cookie so the first paint does not wait on the
/// database; the same reader is used for the cookie and for the stored copy, so a tile
/// class contributed by another team round-trips through both.
/// </summary>
public class WorkspaceModel : PageModel
{
    private readonly Sessions _sessions;
    private readonly Database _database;

    public WorkspaceModel(Sessions sessions, Database database)
    {
        _sessions = sessions;
        _database = database;
    }

    public string DisplayName { get; private set; } = string.Empty;

    public int Columns { get; private set; } = 3;

    public List<string> Tiles { get; private set; } = new();

    public string EncodedState { get; private set; } = string.Empty;

    public int WidthOf(string tile)
    {
        return _widths.TryGetValue(tile, out int width) ? width : 1;
    }

    private readonly Dictionary<string, int> _widths = new(StringComparer.Ordinal);

    public async Task<IActionResult> OnGetAsync()
    {
        PortalUser? user = await _sessions.CurrentAsync(HttpContext);
        if (user is null)
        {
            return Redirect("/signin?returnUrl=/workspace");
        }

        DisplayName = user.DisplayName;

        string? cached = Request.Cookies["wslayout"];
        object? restored = Layouts.Read(cached, Signals.SessionTypeBinding, "the first-paint cookie");
        if (restored is null)
        {
            List<Dictionary<string, object?>> rows = await _database.QueryAsync(
                "SELECT layout FROM workspace_layouts WHERE employee_id = @id", ("id", user.Id));
            string stored = rows.Count == 1
                ? Convert.ToString(rows[0]["layout"], CultureInfo.InvariantCulture) ?? string.Empty
                : string.Empty;
            restored = Layouts.Read(stored, Signals.LayoutTypeBinding, "the saved arrangement");
        }

        LayoutState arrangement = Layouts.AsState(restored);
        Columns = arrangement.Columns;
        foreach (LayoutTile tile in arrangement.Tiles)
        {
            Tiles.Add(tile.Key);
            _widths[tile.Key] = tile.Width;
        }

        EncodedState = Layouts.Write(arrangement);
        return Page();
    }
}
