using System.Globalization;
using Microsoft.AspNetCore.Mvc.RazorPages;

namespace Portal.Pages;

/// <summary>The page a visitor sees when something did not work.</summary>
public class ErrorModel : PageModel
{
    public string Heading { get; private set; } = "Something went wrong";

    public string Explanation { get; private set; } =
        "The portal could not complete that request. Nothing you were working on has been lost.";

    public string Reference { get; private set; } = string.Empty;

    public void OnGet(int? status)
    {
        Reference = HttpContext.TraceIdentifier;
        switch (status)
        {
            case 404:
                Heading = "Page not found";
                Explanation = "That address does not match anything on the portal. It may have moved when the "
                    + "supplier pages were reorganised in January.";
                break;
            case 403:
                Heading = "Not permitted";
                Explanation = "Your account does not have access to that screen. The directory team can grant it.";
                break;
            case 401:
                Heading = "Sign in to continue";
                Explanation = "That screen needs a signed-in account.";
                break;
            default:
                if (status.HasValue)
                {
                    Heading = "Something went wrong ("
                        + status.Value.ToString(CultureInfo.InvariantCulture) + ")";
                }

                break;
        }
    }
}
