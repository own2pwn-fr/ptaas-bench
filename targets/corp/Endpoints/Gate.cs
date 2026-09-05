using Portal.Security;

namespace Portal.Endpoints;

/// <summary>
/// Which verbs a caller may use where.
///
/// The rule is short because the portal only has two operations that destroy anything:
/// withdrawing an approval and closing an account. Both are restricted to the directory
/// administrators, and the restriction is expressed as a refusal of the verb that
/// performs them.
/// </summary>
public static class Gate
{
    /// <summary>Key under which the verb the decision was taken on is recorded.</summary>
    public const string DecidedVerbItem = "portal.decided_verb";

    public static async Task DecideAsync(HttpContext context, RequestDelegate next)
    {
        string verb = context.Request.Method;
        context.Items[DecidedVerbItem] = verb;

        if (IsDestructive(verb, context.Request.Path))
        {
            Sessions sessions = context.RequestServices.GetRequiredService<Sessions>();
            PortalUser? user = await sessions.CurrentAsync(context).ConfigureAwait(false);
            if (user is null || !user.IsAdministrator)
            {
                context.Response.StatusCode = StatusCodes.Status403Forbidden;
                await context.Response.WriteAsync("That operation is restricted to the directory team.")
                    .ConfigureAwait(false);
                return;
            }
        }

        await next(context).ConfigureAwait(false);
    }

    private static bool IsDestructive(string verb, PathString path)
    {
        if (!string.Equals(verb, "DELETE", StringComparison.OrdinalIgnoreCase))
        {
            return false;
        }

        return path.StartsWithSegments("/api/approvals") || path.StartsWithSegments("/account/profile");
    }

    /// <summary>The verb the decision above was taken on.</summary>
    public static string DecidedVerb(HttpContext context)
    {
        return context.Items.TryGetValue(DecidedVerbItem, out object? value) && value is string verb
            ? verb
            : context.Request.Method;
    }
}

/// <summary>
/// The verb a request actually asks for.
///
/// One partner's network appliance strips every verb but GET and POST, so the portal
/// accepts the verb in a header and, for plain browser forms, in a field. Both are read
/// here, at the point the operation is chosen.
/// </summary>
public static class MethodOverride
{
    public const string HeaderName = "X-HTTP-Method-Override";
    public const string FieldName = "_method";

    private static readonly string[] Accepted = { "GET", "POST", "PUT", "PATCH", "DELETE", "HEAD" };

    /// <summary>The verb from the header, or the request's own.</summary>
    public static string FromHeader(HttpContext context)
    {
        string? claimed = context.Request.Headers[HeaderName].ToString();
        return Normalise(claimed) ?? context.Request.Method;
    }

    /// <summary>The verb from a form field, or the request's own.</summary>
    public static string FromForm(HttpContext context, IFormCollection form)
    {
        string? claimed = form.TryGetValue(FieldName, out Microsoft.Extensions.Primitives.StringValues value)
            ? value.ToString()
            : null;
        return Normalise(claimed) ?? context.Request.Method;
    }

    private static string? Normalise(string? claimed)
    {
        if (string.IsNullOrWhiteSpace(claimed))
        {
            return null;
        }

        string upper = claimed.Trim().ToUpperInvariant();
        return Array.IndexOf(Accepted, upper) >= 0 ? upper : null;
    }
}
