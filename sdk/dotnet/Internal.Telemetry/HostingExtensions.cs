using System;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.DependencyInjection;

namespace Internal.Telemetry;

/// <summary>Wiring for an ASP.NET Core service.</summary>
/// <remarks>
/// Two lines in a service:
/// <code>
/// builder.Services.AddTelemetry();
/// app.UseTelemetry();
/// </code>
/// </remarks>
public static class HostingExtensions
{
    /// <summary>
    /// Register the client, its middleware knobs and the accessor the ambient context
    /// falls back on.
    /// </summary>
    /// <param name="services">The service collection.</param>
    /// <param name="configure">Options callback.</param>
    /// <param name="configureMiddleware">Middleware knobs callback.</param>
    /// <returns>The service collection.</returns>
    public static IServiceCollection AddTelemetry(
        this IServiceCollection services,
        Action<TelemetryOptions>? configure = null,
        Action<TelemetryMiddlewareOptions>? configureMiddleware = null)
    {
        if (services is null)
        {
            throw new ArgumentNullException(nameof(services));
        }

        TelemetryOptions options = new();
        configure?.Invoke(options);

        TelemetryMiddlewareOptions middlewareOptions = new();
        configureMiddleware?.Invoke(middlewareOptions);

        // One instance, reachable both by injection and through the static facade, so a
        // helper deep in the service does not have to be handed anything to record.
        TelemetryClient client = Telemetry.Install(new TelemetryClient(options));

        services.AddHttpContextAccessor();
        services.AddSingleton(client);
        services.AddSingleton(middlewareOptions);
        return services;
    }

    /// <summary>
    /// Insert the middleware. Call it first, before routing and before anything that
    /// reads the request body.
    /// </summary>
    /// <param name="app">The application pipeline.</param>
    /// <returns>The application pipeline.</returns>
    public static IApplicationBuilder UseTelemetry(this IApplicationBuilder app)
    {
        if (app is null)
        {
            throw new ArgumentNullException(nameof(app));
        }

        IHttpContextAccessor? accessor = app.ApplicationServices.GetService<IHttpContextAccessor>();
        if (accessor is not null)
        {
            // Second source for the ambient facts. The primary one follows await,
            // Task.Run and the thread pool; this one covers the remaining case where
            // only the HttpContext survived onto the thread doing the work.
            TelemetryContext.Resolver = () =>
            {
                HttpContext? http = accessor.HttpContext;
                if (http is null)
                {
                    return null;
                }

                return http.Items.TryGetValue(TelemetryContext.ItemsKey, out object? found)
                    ? found as RequestContext
                    : null;
            };
        }

        return app.UseMiddleware<TelemetryMiddleware>();
    }
}
