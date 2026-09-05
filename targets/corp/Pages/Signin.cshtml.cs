using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Portal.Endpoints;
using Portal.Security;

namespace Portal.Pages;

/// <summary>
/// The sign-in page.
///
/// The return address is kept between the two requests so that somebody who followed a
/// link into an authenticated screen lands back on it. It is required to be local: the
/// two sibling applications that sign in through this portal do so through the connected
/// applications flow, not through this field.
/// </summary>
public class SigninModel : PageModel
{
    private readonly Sessions _sessions;

    public SigninModel(Sessions sessions)
    {
        _sessions = sessions;
    }

    public bool Failed { get; private set; }

    public string ReturnUrl { get; private set; } = "/workspace";

    public async Task<IActionResult> OnGetAsync(string? returnUrl)
    {
        ReturnUrl = Local(returnUrl);
        PortalUser? user = await _sessions.CurrentAsync(HttpContext);
        return user is null ? Page() : Redirect(ReturnUrl);
    }

    public async Task<IActionResult> OnPostAsync()
    {
        IFormCollection form = await Request.ReadFormAsync(HttpContext.RequestAborted);
        ReturnUrl = Local(form["returnUrl"].ToString());
        string email = form["email"].ToString();
        string password = form["password"].ToString();

        PortalUser? user = await _sessions.SignInAsync(HttpContext, email, password);
        if (user is null)
        {
            Failed = true;
            return Page();
        }

        return Redirect(ReturnUrl);
    }

    /// <summary>Only a path on this portal is accepted here.</summary>
    private string Local(string? candidate)
    {
        string value = (candidate ?? string.Empty).Trim();
        if (value.Length == 0 || !value.StartsWith('/') || value.StartsWith("//", StringComparison.Ordinal))
        {
            return "/workspace";
        }

        return Redirects.IsLocal(HttpContext, value, out _) ? value : "/workspace";
    }
}
