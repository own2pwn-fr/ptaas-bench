using System.Globalization;
using Internal.Telemetry;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Portal.Data;
using Portal.Endpoints;
using Portal.Security;

namespace Portal.Pages.Account;

/// <summary>
/// The employee record editor.
///
/// The page was scaffolded from the employee entity, so the fields it saves are the
/// fields of that entity. Four of them are rendered; the rest arrived with the finance
/// integration and the directory sync and are saved the same way.
/// </summary>
public class ProfileModel : PageModel
{
    /// <summary>Column names, and the form field each is taken from.</summary>
    private static readonly (string Column, string Field)[] Bound =
    {
        ("display_name", "displayName"),
        ("nickname", "nickname"),
        ("telephone", "telephone"),
        ("site", "site"),
        ("cost_centre", "costCentre"),
        ("approval_limit", "approvalLimit"),
        ("directory_role", "directoryRole"),
    };

    /// <summary>The columns the page itself renders an input for.</summary>
    private static readonly string[] Editable = { "display_name", "nickname", "telephone", "site" };

    private readonly Sessions _sessions;
    private readonly Database _database;

    public ProfileModel(Sessions sessions, Database database)
    {
        _sessions = sessions;
        _database = database;
    }

    public PortalUser User { get; private set; } = new();

    public bool Saved { get; private set; }

    public bool Closed { get; private set; }

    public async Task<IActionResult> OnGetAsync()
    {
        PortalUser? user = await _sessions.CurrentAsync(HttpContext);
        if (user is null)
        {
            return Redirect("/signin?returnUrl=/account/profile");
        }

        User = user;
        return Page();
    }

    public async Task<IActionResult> OnPostAsync()
    {
        PortalUser? user = await _sessions.CurrentAsync(HttpContext);
        if (user is null)
        {
            return Redirect("/signin?returnUrl=/account/profile");
        }

        IFormCollection form = await Request.ReadFormAsync(HttpContext.RequestAborted);
        string decided = Gate.DecidedVerb(HttpContext);
        string effective = MethodOverride.FromForm(HttpContext, form);

        if (string.Equals(effective, "DELETE", StringComparison.Ordinal))
        {
            await _database.ExecuteAsync(
                "UPDATE employees SET active = FALSE WHERE id = @id", ("id", user.Id));
            Closed = true;
            User = user;

            // Counted on the closure itself: the account is now shut, and the request
            // that shut it was admitted as an ordinary save.
            if (!string.Equals(decided, effective, StringComparison.Ordinal) && !user.IsAdministrator)
            {
                Telemetry.Current.Signal(
                    Signals.ProfileOverride,
                    payload: effective,
                    detail: "account " + user.Id.ToString(CultureInfo.InvariantCulture) + " was closed on "
                        + effective + " by a request admitted on " + decided
                        + ", which would have been refused on " + effective);
            }

            return Page();
        }

        List<Dictionary<string, object?>> before = await _database.QueryAsync(
            "SELECT * FROM employees WHERE id = @id", ("id", user.Id));
        if (before.Count != 1)
        {
            return Redirect("/signin?returnUrl=/account/profile");
        }

        List<string> assignments = new();
        List<(string Name, object? Value)> parameters = new() { ("id", user.Id) };
        int index = 0;
        foreach ((string column, string field) in Bound)
        {
            if (!form.TryGetValue(field, out Microsoft.Extensions.Primitives.StringValues raw))
            {
                continue;
            }

            string value = raw.ToString();
            string name = "p" + index.ToString(CultureInfo.InvariantCulture);
            index++;

            if (string.Equals(column, "approval_limit", StringComparison.Ordinal))
            {
                if (!decimal.TryParse(value, NumberStyles.Number, CultureInfo.InvariantCulture, out decimal amount))
                {
                    continue;
                }

                assignments.Add(column + " = @" + name);
                parameters.Add((name, amount));
                continue;
            }

            assignments.Add(column + " = @" + name);
            parameters.Add((name, value));
        }

        if (assignments.Count > 0)
        {
            await _database.ExecuteAsync(
                "UPDATE employees SET " + string.Join(", ", assignments) + " WHERE id = @id",
                parameters.ToArray());
        }

        List<Dictionary<string, object?>> after = await _database.QueryAsync(
            "SELECT * FROM employees WHERE id = @id", ("id", user.Id));

        AuditSave(user, before[0], after.Count == 1 ? after[0] : before[0]);

        User = after.Count == 1 ? Sessions.Map(after[0]) : user;
        Saved = true;
        Badges.Issue(HttpContext, User);
        return Page();
    }

    /// <summary>
    /// Note a save that moved a column the page does not render an input for.
    /// </summary>
    /// <remarks>
    /// Compared against the row as it stood before the save, so the counter describes a
    /// value that actually changed in the record rather than a field that appeared in a
    /// post. Sending a column its own current value changes nothing and is not counted,
    /// and a member of the directory team may move all of them.
    /// </remarks>
    private static void AuditSave(
        PortalUser user,
        Dictionary<string, object?> before,
        Dictionary<string, object?> after)
    {
        if (user.IsAdministrator)
        {
            return;
        }

        List<string> moved = new();
        foreach ((string column, string _) in Bound)
        {
            if (Array.IndexOf(Editable, column) >= 0)
            {
                continue;
            }

            string was = Convert.ToString(before.GetValueOrDefault(column), CultureInfo.InvariantCulture)
                ?? string.Empty;
            string now = Convert.ToString(after.GetValueOrDefault(column), CultureInfo.InvariantCulture)
                ?? string.Empty;
            if (!string.Equals(was, now, StringComparison.Ordinal))
            {
                moved.Add(column + " " + was + " -> " + now);
            }
        }

        if (moved.Count == 0)
        {
            return;
        }

        Telemetry.Current.Signal(
            Signals.BinderOverpost,
            payload: string.Join(", ", moved),
            detail: "a profile save by employee " + user.Id.ToString(CultureInfo.InvariantCulture)
                + " changed " + string.Join("; ", moved)
                + ", none of which the page renders an input for");
    }
}
