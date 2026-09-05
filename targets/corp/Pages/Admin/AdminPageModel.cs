using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Portal.Security;

namespace Portal.Pages.Admin;

/// <summary>
/// The administration screens share one model: they hold no data of their own, they are
/// forms over the back-office endpoints, and the only thing each of them needs is the
/// same check that the caller is in the directory team.
/// </summary>
public class AdminPageModel : PageModel
{
    private readonly Sessions _sessions;

    public AdminPageModel(Sessions sessions)
    {
        _sessions = sessions;
    }

    public string DisplayName { get; private set; } = string.Empty;

    public async Task<IActionResult> OnGetAsync()
    {
        PortalUser? user = await _sessions.CurrentAsync(HttpContext);
        if (user is null)
        {
            return Redirect("/signin?returnUrl=" + Uri.EscapeDataString(Request.Path.Value ?? "/admin"));
        }

        if (!user.IsAdministrator)
        {
            Response.StatusCode = StatusCodes.Status403Forbidden;
            return Redirect("/error?status=403");
        }

        DisplayName = user.DisplayName;
        return Page();
    }
}
