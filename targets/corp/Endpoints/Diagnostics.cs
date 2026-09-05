using System.Globalization;
using System.Net;
using System.Text;
using Internal.Telemetry;

namespace Portal.Endpoints;

/// <summary>
/// The detailed error page the analytics team keep attached to the reporting module.
///
/// It renders what the framework's own development page renders - the exception, the
/// frames beneath it and the settings the process was started with - because the point
/// of having it is to answer "why did that export fail" without a log shipment.
/// </summary>
public static class Diagnostics
{
    public static async Task PageAsync(HttpContext context, RequestDelegate next)
    {
        try
        {
            await next(context).ConfigureAwait(false);
        }
        catch (Exception error)
        {
            if (context.Response.HasStarted)
            {
                throw;
            }

            string body = Render(context, error);
            context.Response.Clear();
            context.Response.StatusCode = StatusCodes.Status500InternalServerError;
            context.Response.ContentType = "text/html; charset=utf-8";
            await context.Response.WriteAsync(body).ConfigureAwait(false);

            Audit(context, error, body);
        }
    }

    /// <summary>
    /// Note a detailed page that actually carried internal detail.
    /// </summary>
    /// <remarks>
    /// Raised on the body that was written, not on the fact that something threw. A
    /// handled failure renders the ordinary apology page and says nothing; this counter
    /// only moves when the response a visitor received contained a frame from inside the
    /// process and the settings it was started with.
    /// </remarks>
    private static void Audit(HttpContext context, Exception error, string body)
    {
        bool carriesFrames = body.Contains(" at ", StringComparison.Ordinal)
            && body.Contains("Portal.", StringComparison.Ordinal);
        bool carriesSettings = body.Contains("Host=", StringComparison.OrdinalIgnoreCase)
            || body.Contains("Username=", StringComparison.OrdinalIgnoreCase);
        if (!carriesFrames || !carriesSettings)
        {
            return;
        }

        Telemetry.Current.Signal(
            Signals.ReportsDiagnostic,
            payload: context.Request.QueryString.HasValue ? context.Request.QueryString.Value : context.Request.Path,
            detail: "a " + error.GetType().Name + " rendered the detailed page: "
                + body.Length.ToString(CultureInfo.InvariantCulture)
                + " bytes carrying call frames from inside the process and the data source settings");
    }

    private static string Render(HttpContext context, Exception error)
    {
        StringBuilder page = new();
        page.Append("<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">");
        page.Append("<title>Internal Server Error</title>");
        page.Append("<style>body{font-family:Consolas,\"Courier New\",monospace;font-size:13px;margin:0;}");
        page.Append("h1{background:#44525f;color:#fff;font-size:18px;margin:0;padding:12px 15px;}");
        page.Append("h2{font-size:14px;margin:18px 15px 6px;}pre{margin:0 15px;white-space:pre-wrap;}");
        page.Append("table{border-collapse:collapse;margin:0 15px;}td,th{border:1px solid #ddd;padding:3px 8px;");
        page.Append("text-align:left;font-weight:normal;}</style></head><body>");

        page.Append("<h1>").Append(WebUtility.HtmlEncode(error.GetType().FullName)).Append(": ")
            .Append(WebUtility.HtmlEncode(error.Message)).Append("</h1>");

        page.Append("<h2>Stack</h2><pre>");
        page.Append(WebUtility.HtmlEncode(error.ToString()));
        page.Append("</pre>");

        page.Append("<h2>Request</h2><table>");
        Row(page, "Method", context.Request.Method);
        Row(page, "Path", context.Request.Path.Value ?? "/");
        Row(page, "Query", context.Request.QueryString.Value ?? string.Empty);
        Row(page, "Host", context.Request.Host.Value ?? string.Empty);
        page.Append("</table>");

        page.Append("<h2>Process</h2><table>");
        Row(page, "Environment", Environment.GetEnvironmentVariable("ASPNETCORE_ENVIRONMENT") ?? "Production");
        Row(page, "Machine", Environment.MachineName);
        Row(page, "Framework", Environment.Version.ToString());
        Row(page, "Working set", Environment.WorkingSet.ToString(CultureInfo.InvariantCulture));
        Row(page, "Data source", Environment.GetEnvironmentVariable("PORTAL_DATABASE") ?? "(default)");
        Row(page, "Uploads", Environment.GetEnvironmentVariable("PORTAL_UPLOADS") ?? "/var/lib/portal/uploads");
        Row(page, "Telemetry", Environment.GetEnvironmentVariable("TELEMETRY_ENDPOINT") ?? "(none)");
        page.Append("</table>");

        page.Append("</body></html>");
        return page.ToString();
    }

    private static void Row(StringBuilder page, string name, string value)
    {
        page.Append("<tr><th>").Append(WebUtility.HtmlEncode(name)).Append("</th><td>")
            .Append(WebUtility.HtmlEncode(value)).Append("</td></tr>");
    }
}
