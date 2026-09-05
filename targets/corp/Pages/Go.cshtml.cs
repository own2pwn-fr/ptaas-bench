using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Portal.Endpoints;

namespace Portal.Pages;

/// <summary>
/// The interstitial the newsroom and the supplier pages link through.
///
/// Communications asked for it so that outbound clicks can be counted without a
/// third-party script on every page, which is why every external link on the site is
/// written as a link to this page.
/// </summary>
public class GoModel : PageModel
{
    public IActionResult OnGet(string? to)
    {
        string target = (to ?? string.Empty).Trim();
        if (target.Length == 0)
        {
            return Page();
        }

        Redirects.Audit(HttpContext, target, Signals.LinkOffsite, "to");
        return Redirect(target);
    }
}
