using System.Globalization;
using Internal.Telemetry;
using Npgsql;
using Portal;
using Portal.Data;
using Portal.Endpoints;
using Portal.Security;
using Portal.Services;

WebApplicationBuilder builder = WebApplication.CreateBuilder(args);

string connectionString = Setting("PORTAL_DATABASE")
    ?? "Host=corp-db;Port=5432;Database=portal;Username=portal;Password=portal;Include Error Detail=true";
int httpPort = Number("PORTAL_PORT", 80);
int controlPort = Number("PORTAL_CONTROL_PORT", 9310);
int converterPort = Number("PORTAL_CONVERTER_PORT", 9311);
string uploadRoot = Setting("PORTAL_UPLOADS") ?? "/var/lib/portal/uploads";
string templateRoot = Setting("PORTAL_TEMPLATES") ?? "/var/lib/portal/templates";

builder.WebHost.ConfigureKestrel(kestrel =>
{
    // The portal answers on the port a portal answers on. The container runs
    // unprivileged, so the deployment lowers the unprivileged port floor for this
    // network namespace rather than handing the process a capability it would keep for
    // everything else; see the sysctl in the deployment file.
    kestrel.ListenAnyIP(httpPort);

    // Operations listener. It is bound to the loopback address inside the container so
    // that reseeding and the state digest cannot be driven from the network; the same
    // reasoning as the shell account that runs them.
    kestrel.ListenLocalhost(controlPort);
});

DeploymentSeed seed = new(Setting("DEPLOY_SEED"));
Badges.UseKey(seed.Key("badge", 16));
ShareTokens.UseKeys(seed.Key("share"), seed.Key("share-check"));

builder.Services.AddSingleton(seed);
builder.Services.AddSingleton(new NpgsqlDataSourceBuilder(connectionString).Build());
builder.Services.AddSingleton<Database>();
builder.Services.AddScoped<Sessions>();
builder.Services.AddSingleton(new DocumentStore(uploadRoot, templateRoot));
builder.Services.AddSingleton<OutboundProbe>();
builder.Services.AddSingleton<DirectoryImport>();
builder.Services.AddSingleton(new ConverterClient(converterPort));
builder.Services.AddScoped<UpdateChannel>();
builder.Services.AddHostedService(_ => new ConverterDaemon(converterPort));
builder.Services.AddRazorPages();
builder.Services.AddHealthChecks();

// Reads TELEMETRY_SERVICE and TELEMETRY_ENDPOINT. With neither set the client is inert
// and the portal behaves exactly as it does when the collector is unreachable.
builder.Services.AddTelemetry(
    configureMiddleware: middleware =>
    {
        // The operations listener is not part of the service's traffic.
        middleware.Ignore = context => context.Connection.LocalPort == controlPort;
    });

WebApplication app = builder.Build();

// First, ahead of everything that reads a body: the request has to be made re-readable
// before model binding takes it.
app.UseTelemetry();

// The operations listener answers its own paths and nothing else, and the service port
// answers everything except them. A difference that is visible from outside would be an
// invitation, so the answer either way is an ordinary not-found.
app.Use(async (context, next) =>
{
    bool onOperationsPort = context.Connection.LocalPort == controlPort;
    bool operationsPath = context.Request.Path.StartsWithSegments("/ops");
    if (onOperationsPort != operationsPath)
    {
        context.Response.StatusCode = StatusCodes.Status404NotFound;
        return;
    }

    await next().ConfigureAwait(false);
});

app.UseStaticFiles();

// The apology page every other area answers with. It is registered ahead of the
// reporting module's own handler on purpose: middleware nearest the endpoint sees a
// failure first, so registering it the other way round would mean the detailed page
// below never ran.
app.UseExceptionHandler(new ExceptionHandlerOptions
{
    ExceptionHandlingPath = "/error",
    AllowStatusCode404Response = true,
});
app.UseStatusCodePagesWithReExecute("/error", "?status={0}");

// The reporting module is maintained by the analytics team, who keep the detailed error
// page attached on this deployment so that they can read a failure from the running
// instance without waiting for a log shipment.
app.UseWhen(
    context => context.Request.Path.StartsWithSegments("/reports"),
    branch => branch.Use(Diagnostics.PageAsync));

app.UseRouting();

// The authorisation decision is taken here, on the verb the request arrived with.
app.Use(Gate.DecideAsync);

app.MapRazorPages();
Api.Map(app);
Operations.Map(app);

await Startup.PrepareAsync(app).ConfigureAwait(false);

app.Run();

static string? Setting(string name)
{
    string? value = Environment.GetEnvironmentVariable(name);
    return string.IsNullOrWhiteSpace(value) ? null : value.Trim();
}

static int Number(string name, int fallback)
{
    string? value = Setting(name);
    return value is not null
        && int.TryParse(value, NumberStyles.Integer, CultureInfo.InvariantCulture, out int parsed)
        ? parsed
        : fallback;
}

/// <summary>Marker so the test host and the page classes can name this assembly.</summary>
public partial class Program
{
}
