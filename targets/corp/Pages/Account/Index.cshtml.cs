using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Portal.Security;

namespace Portal.Pages.Account;

/// <summary>The account overview.</summary>
public class IndexModel : PageModel
{
    private readonly Sessions _sessions;

    public IndexModel(Sessions sessions)
    {
        _sessions = sessions;
    }

    public PortalUser User { get; private set; } = new();

    public async Task<IActionResult> OnGetAsync()
    {
        PortalUser? user = await _sessions.CurrentAsync(HttpContext);
        if (user is null)
        {
            return Redirect("/signin?returnUrl=/account");
        }

        User = user;
        return Page();
    }
}
