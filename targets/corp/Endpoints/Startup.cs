using Npgsql;
using Portal.Data;

namespace Portal.Endpoints;

/// <summary>First-run preparation: wait for the database, and create it if it is empty.</summary>
public static class Startup
{
    public static async Task PrepareAsync(WebApplication app)
    {
        Database database = app.Services.GetRequiredService<Database>();
        DeploymentSeed seed = app.Services.GetRequiredService<DeploymentSeed>();

        for (int attempt = 0; attempt < 60; attempt++)
        {
            try
            {
                await using NpgsqlConnection connection = await database.OpenAsync().ConfigureAwait(false);
                break;
            }
            catch (Exception)
            {
                await Task.Delay(TimeSpan.FromSeconds(1)).ConfigureAwait(false);
            }
        }

        bool present;
        try
        {
            object? scalar = await database
                .ScalarAsync("SELECT to_regclass('public.employees') IS NOT NULL")
                .ConfigureAwait(false);
            present = scalar is bool value && value;
        }
        catch (Exception)
        {
            present = false;
        }

        if (!present)
        {
            await Seed.ApplyAsync(database, seed).ConfigureAwait(false);
        }
    }
}
