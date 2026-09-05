using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Portal.Security;

namespace Portal.Pages;

/// <summary>Ends the session on this device.</summary>
public class SignoutModel : PageModel
{
    private readonly Sessions _sessions;

    public SignoutModel(Sessions sessions)
    {
        _sessions = sessions;
    }

    public async Task<IActionResult> OnGetAsync()
    {
        await _sessions.SignOutAsync(HttpContext);
        return Page();
    }

    public async Task<IActionResult> OnPostAsync()
    {
        await _sessions.SignOutAsync(HttpContext);
        return Page();
    }
}
