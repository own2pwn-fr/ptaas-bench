using System.Globalization;
using System.Text;
using Portal.Data;
using Portal.Security;
using Portal.Services;

namespace Portal.Endpoints;

/// <summary>
/// The operations listener, bound to the loopback address inside the container.
///
/// It reseeds the instance and prints the digest of the state that is supposed to be
/// constant. It is not a route on the service port on purpose: anything reachable from
/// the network can be driven by a visitor, and an instance that a visitor can empty is
/// not an instance anybody can support.
/// </summary>
public static class Operations
{
    public static void Map(WebApplication app)
    {
        app.MapGet("/ops/health", () => Results.Text("ok\n", "text/plain"));

        app.MapPost("/ops/reset", async (HttpContext context) =>
        {
            Database database = context.RequestServices.GetRequiredService<Database>();
            DeploymentSeed seed = context.RequestServices.GetRequiredService<DeploymentSeed>();
            DocumentStore store = context.RequestServices.GetRequiredService<DocumentStore>();

            await Seed.ApplyAsync(database, seed).ConfigureAwait(false);
            RestoreFiles(store, seed, database);
            ShareTokens.Forget();

            string digest = await database.DigestAsync(store.UploadRoot, store.TemplateRoot).ConfigureAwait(false);
            return Results.Text("state " + digest + "\n", "text/plain");
        });

        app.MapGet("/ops/digest", async (HttpContext context) =>
        {
            Database database = context.RequestServices.GetRequiredService<Database>();
            DocumentStore store = context.RequestServices.GetRequiredService<DocumentStore>();
            string digest = await database.DigestAsync(store.UploadRoot, store.TemplateRoot).ConfigureAwait(false);
            return Results.Text("state " + digest + "\n", "text/plain");
        });
    }

    private static void RestoreFiles(DocumentStore store, DeploymentSeed seed, Database database)
    {
        // Anything an upload put outside its own store goes first: a file left behind
        // there would survive the reseed and make two runs of the same release differ.
        foreach (string stray in DocumentStore.StrayWrites)
        {
            try
            {
                if (File.Exists(stray))
                {
                    File.Delete(stray);
                }
            }
            catch (Exception)
            {
                // A file the process cannot remove is reported by the digest instead.
            }
        }

        DocumentStore.StrayWrites.Clear();

        Recreate(store.UploadRoot);
        Recreate(store.TemplateRoot);

        // The seeded asset set, rebuilt exactly. Names come from the same derivation the
        // database rows use, so the two always agree.
        List<Dictionary<string, object?>> documents = database
            .QueryAsync("SELECT id, title, stored_name FROM documents ORDER BY id")
            .GetAwaiter()
            .GetResult();

        foreach (Dictionary<string, object?> row in documents)
        {
            string name = Convert.ToString(row["stored_name"], CultureInfo.InvariantCulture) ?? string.Empty;
            string title = Convert.ToString(row["title"], CultureInfo.InvariantCulture) ?? string.Empty;
            if (name.Length == 0)
            {
                continue;
            }

            File.WriteAllText(
                Path.Combine(store.UploadRoot, Path.GetFileName(name)),
                "%PDF-1.4\n% Meridian Castings document store\n% " + title + "\n%%EOF\n",
                Encoding.ASCII);
        }

        File.WriteAllText(
            Path.Combine(store.UploadRoot, "meridian-mark.svg"),
            "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 64 64\" width=\"64\" height=\"64\">"
            + "<rect width=\"64\" height=\"64\" fill=\"#ffffff\"/>"
            + "<path d=\"M12 50V14h7l13 22 13-22h7v36h-7V28L32 50 19 28v22z\" fill=\"#1f4e79\"/></svg>\n",
            Encoding.UTF8);

        string[][] templates =
        {
            new[] { "despatch-note", "Despatch note for {{order}} - {{site}}\nRaised by {{employee}}\n" },
            new[] { "supplier-letter", "Dear {{supplier}},\n\nRe: purchase order {{order}}.\n" },
            new[] { "shift-handover", "Shift {{shift}} handover, {{site}}\nOutstanding: {{notes}}\n" },
        };

        foreach (string[] template in templates)
        {
            File.WriteAllText(
                Path.Combine(store.TemplateRoot, template[0] + ".txt"),
                template[1] + "Reference " + seed.Text("template:" + template[0], 8) + "\n",
                Encoding.UTF8);
        }
    }

    private static void Recreate(string root)
    {
        try
        {
            if (Directory.Exists(root))
            {
                Directory.Delete(root, recursive: true);
            }
        }
        catch (Exception)
        {
            // Fall through: the directory is recreated below and the digest will show
            // whatever survived.
        }

        Directory.CreateDirectory(root);
    }
}
