using System.Globalization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Portal.Data;
using Portal.Security;

namespace Portal.Pages.Reports;

/// <summary>
/// The spreadsheet download.
///
/// Three writers are wired in. A format that is not one of them is a caller mistake and
/// the writer factory says so; the analytics team read the resulting page directly from
/// the running instance rather than waiting for a log shipment.
/// </summary>
public class ExportModel : PageModel
{
    private static readonly string[] Writers = { "xlsx", "csv", "pdf" };

    private readonly Sessions _sessions;
    private readonly Database _database;

    public ExportModel(Sessions sessions, Database database)
    {
        _sessions = sessions;
        _database = database;
    }

    public string Format { get; private set; } = "xlsx";

    public int Rows { get; private set; }

    public async Task<IActionResult> OnGetAsync(string? format)
    {
        PortalUser? user = await _sessions.CurrentAsync(HttpContext);
        if (user is null)
        {
            return Redirect("/signin?returnUrl=/reports/export");
        }

        Format = (format ?? "xlsx").Trim();
        if (Array.IndexOf(Writers, Format) < 0)
        {
            throw new NotSupportedException(
                "No writer is registered for the '" + Format + "' format. Registered writers: "
                + string.Join(", ", Writers) + ".");
        }

        object? scalar = await _database.ScalarAsync(
            "SELECT count(*) FROM timesheets WHERE cost_centre = @cc", ("cc", user.CostCentre));
        Rows = Convert.ToInt32(scalar, CultureInfo.InvariantCulture);
        return Page();
    }
}
