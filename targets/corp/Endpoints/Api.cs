using System.Globalization;
using System.Text;
using System.Text.Json;
using Internal.Telemetry;
using Portal.Data;
using Portal.Security;
using Portal.Services;

namespace Portal.Endpoints;

/// <summary>
/// The JSON surface behind the portal's screens.
///
/// It is not a public API: it exists because several screens are richer than a form
/// post, and because the desktop agent and the shift board read from it. There is no
/// published specification, which is why the shapes here are shaped like the screens
/// that consume them rather than like the tables underneath.
/// </summary>
public static class Api
{
    public static void Map(WebApplication app)
    {
        MapPublic(app);
        MapAccount(app);
        MapDocuments(app);
        MapApprovals(app);
        MapTeams(app);
        MapReports(app);
        MapAdministration(app);
    }

    // ---------------------------------------------------------------- anonymous

    private static void MapPublic(WebApplication app)
    {
        app.MapGet("/api/status", () => Results.Json(new
        {
            service = "portal",
            release = "4.2.7",
            state = "ok",
        }));

        // First-party page views. The counter is written straight to the log shipper and
        // nothing about the visitor is kept.
        app.MapPost("/api/telemetry/pageview", async (HttpContext context) =>
        {
            JsonElement? body = await ReadJsonAsync(context).ConfigureAwait(false);
            string path = Text(body, "path");
            return Results.Json(new { recorded = path.Length > 0 });
        });

        app.MapGet("/api/news", async (HttpContext context) =>
        {
            Database database = Db(context);
            List<Dictionary<string, object?>> rows = await database
                .QueryAsync("SELECT id, headline, published FROM news ORDER BY published DESC")
                .ConfigureAwait(false);
            return Results.Json(rows);
        });

        app.MapGet("/api/news/{id:int}", async (HttpContext context, int id) =>
        {
            List<Dictionary<string, object?>> rows = await Db(context)
                .QueryAsync("SELECT id, headline, body, published FROM news WHERE id = @id", ("id", id))
                .ConfigureAwait(false);
            return rows.Count == 1 ? Results.Json(rows[0]) : Results.NotFound();
        });

        app.MapGet("/api/offices", () => Results.Json(new[]
        {
            new { site = "sheffield", address = "Riverside Way, Attercliffe, Sheffield S9 2FL", telephone = "0114 496 2200" },
            new { site = "wrexham", address = "Bryn Lane, Wrexham Industrial Estate, LL13 9UT", telephone = "01978 660 140" },
            new { site = "poznan", address = "ul. Odlewnicza 8, 61-003 Poznan", telephone = "+48 61 887 4100" },
        }));

        app.MapGet("/api/products", () => Results.Json(new[]
        {
            new { key = "castings", name = "Castings", lead_time_weeks = 6 },
            new { key = "machining", name = "Machining", lead_time_weeks = 3 },
            new { key = "finishing", name = "Finishing", lead_time_weeks = 2 },
            new { key = "tooling", name = "Tooling", lead_time_weeks = 9 },
        }));

        app.MapGet("/api/help/topics", () => Results.Json(new[]
        {
            new { slug = "onboarding", title = "New starter onboarding" },
            new { slug = "equipment", title = "Equipment requests" },
            new { slug = "directory", title = "Staff directory" },
            new { slug = "expenses", title = "Expense claims" },
        }));

        app.MapGet("/api/help/topics/{slug}", (string slug) =>
        {
            string[] known = { "onboarding", "equipment", "directory", "expenses" };
            return Array.IndexOf(known, slug) < 0
                ? Results.NotFound()
                : Results.Json(new { slug, body = "See the help centre page for " + slug + "." });
        });

        app.MapPost("/api/contact", async (HttpContext context) =>
        {
            JsonElement? body = await ReadJsonAsync(context).ConfigureAwait(false);
            string from = Text(body, "email");
            string message = Text(body, "message");
            if (from.Length == 0 || message.Length == 0)
            {
                return Results.BadRequest(new { error = "an address and a message are needed" });
            }

            return Results.Json(new { received = true, reference = "CT-" + DateTime.UtcNow.Ticks % 1000000 });
        });

        app.MapPost("/api/careers/apply", async (HttpContext context) =>
        {
            JsonElement? body = await ReadJsonAsync(context).ConfigureAwait(false);
            string role = Text(body, "role");
            return role.Length == 0
                ? Results.BadRequest(new { error = "a role is needed" })
                : Results.Json(new { received = true, role });
        });

        app.MapGet("/api/directory/search", async (HttpContext context, string? q) =>
        {
            string term = (q ?? string.Empty).Trim();
            if (term.Length < 2)
            {
                return Results.Json(Array.Empty<object>());
            }

            List<Dictionary<string, object?>> rows = await Db(context).QueryAsync(
                "SELECT id, display_name, site, telephone FROM employees"
                + " WHERE active AND display_name ILIKE @term ORDER BY display_name LIMIT 25",
                ("term", "%" + term + "%")).ConfigureAwait(false);
            return Results.Json(rows);
        });

        // ---- the asset route -----------------------------------------------------
        // Suppliers send drawings as vector files and the document viewer shows them in
        // place, so an asset is served with the type it was stored under rather than as
        // a download. The stored name is always a bare name taken from the row, never
        // from the address.
        app.MapGet("/media/{name}", async (HttpContext context, string name) =>
        {
            DocumentStore store = context.RequestServices.GetRequiredService<DocumentStore>();
            string bare = Path.GetFileName(name);
            byte[]? bytes = await store.ReadAssetAsync(bare, context.RequestAborted).ConfigureAwait(false);
            if (bytes is null)
            {
                return Results.NotFound();
            }

            string contentType = ContentTypeFor(bare);
            string disposition = "inline; filename=\"" + bare.Replace("\"", string.Empty) + "\"";
            context.Response.Headers.ContentDisposition = disposition;
            DocumentStore.AuditServe(bare, contentType, disposition, bytes);
            return Results.Bytes(bytes, contentType);
        });
    }

    // ------------------------------------------------------------------- account

    private static void MapAccount(WebApplication app)
    {
        app.MapGet("/api/account", async (HttpContext context) =>
        {
            PortalUser? user = await UserAsync(context).ConfigureAwait(false);
            if (user is null)
            {
                return Unauthorised();
            }

            return Results.Json(new
            {
                id = user.Id,
                email = user.Email,
                display_name = user.DisplayName,
                nickname = user.Nickname,
                telephone = user.Telephone,
                site = user.Site,
                cost_centre = user.CostCentre,
                approval_limit = user.ApprovalLimit,
                directory_role = user.DirectoryRole,
            });
        });

        app.MapGet("/api/account/sessions", async (HttpContext context) =>
        {
            PortalUser? user = await UserAsync(context).ConfigureAwait(false);
            if (user is null)
            {
                return Unauthorised();
            }

            List<Dictionary<string, object?>> rows = await Db(context).QueryAsync(
                "SELECT created_at FROM sessions WHERE employee_id = @id ORDER BY created_at DESC LIMIT 10",
                ("id", user.Id)).ConfigureAwait(false);
            return Results.Json(rows);
        });

        app.MapGet("/api/workspace/layout", async (HttpContext context) =>
        {
            PortalUser? user = await UserAsync(context).ConfigureAwait(false);
            if (user is null)
            {
                return Unauthorised();
            }

            List<Dictionary<string, object?>> rows = await Db(context).QueryAsync(
                "SELECT layout FROM workspace_layouts WHERE employee_id = @id", ("id", user.Id))
                .ConfigureAwait(false);
            string stored = rows.Count == 1
                ? Convert.ToString(rows[0]["layout"], CultureInfo.InvariantCulture) ?? string.Empty
                : Layouts.Write(Layouts.Default());
            return Results.Json(new { state = stored });
        });

        // ---- saving the workspace arrangement --------------------------------------
        // The blob is written by the browser and carries the class names of the tiles it
        // holds, so tiles contributed by other teams round-trip instead of collapsing.
        app.MapPost("/api/workspace/layout", async (HttpContext context) =>
        {
            PortalUser? user = await UserAsync(context).ConfigureAwait(false);
            if (user is null)
            {
                return Unauthorised();
            }

            JsonElement? body = await ReadJsonAsync(context).ConfigureAwait(false);
            string state = Text(body, "state");
            if (state.Length == 0)
            {
                return Results.BadRequest(new { error = "a layout is needed" });
            }

            object? restored = Layouts.Read(state, Signals.LayoutTypeBinding, "the saved arrangement");
            LayoutState arrangement = Layouts.AsState(restored);

            await Db(context).ExecuteAsync(
                "INSERT INTO workspace_layouts (employee_id, layout) VALUES (@id, @layout)"
                + " ON CONFLICT (employee_id) DO UPDATE SET layout = EXCLUDED.layout",
                ("id", user.Id),
                ("layout", state)).ConfigureAwait(false);

            // Kept alongside so the first paint does not wait on the database.
            context.Response.Cookies.Append("wslayout", state, new CookieOptions
            {
                HttpOnly = false,
                SameSite = SameSiteMode.Lax,
                Path = "/",
                IsEssential = true,
            });

            return Results.Json(new { saved = true, columns = arrangement.Columns, tiles = arrangement.Tiles.Count });
        });
    }

    // ----------------------------------------------------------------- documents

    private static void MapDocuments(WebApplication app)
    {
        app.MapGet("/api/documents", async (HttpContext context) =>
        {
            PortalUser? user = await UserAsync(context).ConfigureAwait(false);
            if (user is null)
            {
                return Unauthorised();
            }

            List<Dictionary<string, object?>> rows = await Db(context).QueryAsync(
                "SELECT id, title, stored_name, content_type, cost_centre, created_at FROM documents"
                + " ORDER BY id LIMIT 60").ConfigureAwait(false);
            return Results.Json(rows);
        });

        app.MapGet("/api/documents/{id:int}", async (HttpContext context, int id) =>
        {
            PortalUser? user = await UserAsync(context).ConfigureAwait(false);
            if (user is null)
            {
                return Unauthorised();
            }

            List<Dictionary<string, object?>> rows = await Db(context).QueryAsync(
                "SELECT id, title, stored_name, content_type, owner_id, cost_centre, created_at"
                + " FROM documents WHERE id = @id", ("id", id)).ConfigureAwait(false);
            return rows.Count == 1 ? Results.Json(rows[0]) : Results.NotFound();
        });

        // ---- upload ----------------------------------------------------------------
        // Finance asked years ago for the sender's own file name to be kept, so that a
        // despatch note arriving by e-mail and the same note here are the same file. The
        // review at the time settled on an extension deny list.
        app.MapPost("/api/documents", async (HttpContext context) =>
        {
            PortalUser? user = await UserAsync(context).ConfigureAwait(false);
            if (user is null)
            {
                return Unauthorised();
            }

            if (!context.Request.HasFormContentType)
            {
                return Results.BadRequest(new { error = "an upload is needed" });
            }

            IFormCollection form = await context.Request.ReadFormAsync(context.RequestAborted)
                .ConfigureAwait(false);
            IFormFile? file = form.Files.Count > 0 ? form.Files[0] : null;
            string name = form.TryGetValue("name", out Microsoft.Extensions.Primitives.StringValues raw)
                ? raw.ToString()
                : file?.FileName ?? string.Empty;
            string title = form.TryGetValue("title", out Microsoft.Extensions.Primitives.StringValues rawTitle)
                ? rawTitle.ToString()
                : name;

            if (file is null || name.Length == 0)
            {
                return Results.BadRequest(new { error = "a file and a name are needed" });
            }

            DocumentStore store = context.RequestServices.GetRequiredService<DocumentStore>();
            if (store.ExtensionRefused(name))
            {
                return Results.BadRequest(new { error = "that kind of file is not accepted" });
            }

            await using Stream content = file.OpenReadStream();
            await store.SaveUploadAsync(name, content, context.RequestAborted).ConfigureAwait(false);

            string stored = Path.GetFileName(name);
            Database database = Db(context);
            object? next = await database.ScalarAsync("SELECT COALESCE(max(id), 0) + 1 FROM documents")
                .ConfigureAwait(false);
            int id = Convert.ToInt32(next, CultureInfo.InvariantCulture);
            await database.ExecuteAsync(
                "INSERT INTO documents (id, title, stored_name, content_type, owner_id, cost_centre, created_at)"
                + " VALUES (@id, @title, @stored, @ct, @owner, @cc, now())",
                ("id", id),
                ("title", title.Length == 0 ? stored : title),
                ("stored", stored),
                ("ct", ContentTypeFor(stored)),
                ("owner", user.Id),
                ("cc", user.CostCentre)).ConfigureAwait(false);

            return Results.Json(new { id, stored_name = stored, media = "/media/" + stored });
        });

        // ---- render ----------------------------------------------------------------
        // The converter speaks its own dialect, so the job is written out by hand.
        app.MapPost("/api/documents/{id:int}/render", async (HttpContext context, int id) =>
        {
            PortalUser? user = await UserAsync(context).ConfigureAwait(false);
            if (user is null)
            {
                return Unauthorised();
            }

            JsonElement? body = await ReadJsonAsync(context).ConfigureAwait(false);
            string profile = Text(body, "profile");
            if (profile.Length == 0)
            {
                profile = "print-a4";
            }

            ConverterClient converter = context.RequestServices.GetRequiredService<ConverterClient>();
            RenderReply reply = await converter.RenderAsync(id, profile, user.Id, context.RequestAborted)
                .ConfigureAwait(false);
            if (!reply.Completed)
            {
                return Results.Json(new { queued = false, error = "the converter did not answer" });
            }

            return Results.Json(new
            {
                queued = true,
                document = id,
                profile,
                diagnostics = reply.Fields,
            });
        });

        // ---- handoff ---------------------------------------------------------------
        // The viewer sends a document to the print desk, which runs on another host, and
        // brings the reader back afterwards.
        app.MapPost("/api/documents/handoff", async (HttpContext context) =>
        {
            PortalUser? user = await UserAsync(context).ConfigureAwait(false);
            if (user is null)
            {
                return Unauthorised();
            }

            JsonElement? body = await ReadJsonAsync(context).ConfigureAwait(false);
            string next = Text(body, "next");
            if (next.Length == 0)
            {
                next = "/documents";
            }

            Redirects.Audit(context, next, Signals.HandoffOffsite, "next");
            return Results.Redirect(next);
        });
    }

    // ----------------------------------------------------------------- approvals

    private static void MapApprovals(WebApplication app)
    {
        app.MapGet("/api/approvals", async (HttpContext context) =>
        {
            PortalUser? user = await UserAsync(context).ConfigureAwait(false);
            if (user is null)
            {
                return Unauthorised();
            }

            List<Dictionary<string, object?>> rows = await Db(context).QueryAsync(
                "SELECT id, reference, requested_by, amount, state FROM approvals"
                + " WHERE requested_by = @id ORDER BY id", ("id", user.Id)).ConfigureAwait(false);
            return Results.Json(rows);
        });

        app.MapGet("/api/approvals/{id:int}", async (HttpContext context, int id) =>
        {
            PortalUser? user = await UserAsync(context).ConfigureAwait(false);
            if (user is null)
            {
                return Unauthorised();
            }

            List<Dictionary<string, object?>> rows = await Db(context).QueryAsync(
                "SELECT id, reference, requested_by, amount, state FROM approvals"
                + " WHERE id = @id AND requested_by = @who", ("id", id), ("who", user.Id))
                .ConfigureAwait(false);
            return rows.Count == 1 ? Results.Json(rows[0]) : Results.NotFound();
        });

        // ---- the queue the second-line service reads ---------------------------------
        // The approval role travels in the badge because the queue is also served to that
        // service, which decrypts the fields it needs and ignores the rest.
        app.MapGet("/api/approvals/queue", async (HttpContext context) =>
        {
            PortalUser? user = await UserAsync(context).ConfigureAwait(false);
            if (user is null)
            {
                return Unauthorised();
            }

            string role = Badges.Authorise(context, user, served: false);
            if (!string.Equals(role, "approver", StringComparison.Ordinal))
            {
                return Results.StatusCode(StatusCodes.Status403Forbidden);
            }

            List<Dictionary<string, object?>> rows = await Db(context).QueryAsync(
                "SELECT a.id, a.reference, a.amount, a.state, e.display_name, e.cost_centre"
                + " FROM approvals a JOIN employees e ON e.id = a.requested_by"
                + " WHERE a.state = 'pending' ORDER BY a.id").ConfigureAwait(false);

            // Counted once the queue has actually been assembled for this caller.
            Badges.Authorise(context, user, served: true);
            return Results.Json(rows);
        });

        // ---- decide on one -----------------------------------------------------------
        // One handler for the three verbs, because the second-line service and the portal
        // disagree about which of them means what.
        app.MapMethods("/api/approvals/{id}", new[] { "POST", "PUT", "DELETE" }, async (HttpContext context, string id) =>
        {
            PortalUser? user = await UserAsync(context).ConfigureAwait(false);
            if (user is null)
            {
                return Unauthorised();
            }

            if (!int.TryParse(id, NumberStyles.Integer, CultureInfo.InvariantCulture, out int approvalId))
            {
                return Results.NotFound();
            }

            string decided = Gate.DecidedVerb(context);
            string effective = MethodOverride.FromHeader(context);
            Database database = Db(context);

            if (string.Equals(effective, "DELETE", StringComparison.Ordinal))
            {
                object? before = await database
                    .ScalarAsync("SELECT count(*) FROM approvals WHERE id = @id", ("id", approvalId))
                    .ConfigureAwait(false);
                if (Convert.ToInt32(before, CultureInfo.InvariantCulture) == 0)
                {
                    return Results.NotFound();
                }

                await database.ExecuteAsync("DELETE FROM approvals WHERE id = @id", ("id", approvalId))
                    .ConfigureAwait(false);

                // Counted on the withdrawal itself, and only when the verb that performed
                // it is not the verb the request was admitted on.
                if (!string.Equals(decided, effective, StringComparison.Ordinal) && !user.IsAdministrator)
                {
                    Telemetry.Current.Signal(
                        Signals.ApprovalsOverride,
                        payload: effective,
                        detail: "request " + approvalId.ToString(CultureInfo.InvariantCulture)
                            + " was withdrawn on " + effective + " by employee "
                            + user.Id.ToString(CultureInfo.InvariantCulture)
                            + ", who was admitted on " + decided
                            + " and would have been refused on " + effective);
                }

                return Results.Json(new { id = approvalId, state = "withdrawn" });
            }

            await database.ExecuteAsync(
                "UPDATE approvals SET state = 'approved' WHERE id = @id AND requested_by <> @who",
                ("id", approvalId), ("who", user.Id)).ConfigureAwait(false);
            return Results.Json(new { id = approvalId, state = "approved" });
        });
    }

    // --------------------------------------------------------------------- teams

    private static void MapTeams(WebApplication app)
    {
        app.MapGet("/api/teams", async (HttpContext context) =>
        {
            List<Dictionary<string, object?>> rows = await Db(context)
                .QueryAsync("SELECT id, name, cost_centre FROM teams ORDER BY id").ConfigureAwait(false);
            return Results.Json(rows);
        });

        app.MapGet("/api/teams/{id:int}", async (HttpContext context, int id) =>
        {
            List<Dictionary<string, object?>> rows = await Db(context)
                .QueryAsync("SELECT id, name, cost_centre FROM teams WHERE id = @id", ("id", id))
                .ConfigureAwait(false);
            return rows.Count == 1 ? Results.Json(rows[0]) : Results.NotFound();
        });

        app.MapGet("/api/teams/{id:int}/members", async (HttpContext context, int id) =>
        {
            List<Dictionary<string, object?>> rows = await Db(context).QueryAsync(
                "SELECT m.employee_id, e.display_name, m.job_title, m.role FROM team_members m"
                + " JOIN employees e ON e.id = m.employee_id WHERE m.team_id = @id ORDER BY m.employee_id",
                ("id", id)).ConfigureAwait(false);
            return Results.Json(rows);
        });

        // ---- membership save ---------------------------------------------------------
        // The membership screen posts the whole record back, because owners edit the role
        // from the same screen as everybody else edits the job title.
        app.MapMethods("/api/teams/{id:int}/members", new[] { "PATCH" }, async (HttpContext context, int id) =>
        {
            PortalUser? user = await UserAsync(context).ConfigureAwait(false);
            if (user is null)
            {
                return Unauthorised();
            }

            JsonElement? body = await ReadJsonAsync(context).ConfigureAwait(false);
            int memberId = Number(body, "memberId", user.Id);
            string jobTitle = Text(body, "jobTitle");
            string role = Text(body, "role");

            Database database = Db(context);
            List<Dictionary<string, object?>> existing = await database.QueryAsync(
                "SELECT job_title, role FROM team_members WHERE team_id = @t AND employee_id = @e",
                ("t", id), ("e", memberId)).ConfigureAwait(false);
            if (existing.Count != 1)
            {
                return Results.NotFound();
            }

            string previousRole = Convert.ToString(existing[0]["role"], CultureInfo.InvariantCulture) ?? "member";
            string nextRole = role.Length == 0 ? previousRole : role;

            await database.ExecuteAsync(
                "UPDATE team_members SET job_title = @j, role = @r WHERE team_id = @t AND employee_id = @e",
                ("j", jobTitle.Length == 0
                    ? Convert.ToString(existing[0]["job_title"], CultureInfo.InvariantCulture) ?? string.Empty
                    : jobTitle),
                ("r", nextRole),
                ("t", id),
                ("e", memberId)).ConfigureAwait(false);

            // Counted on the row as it now stands: the role actually moved, and the
            // caller is not an owner of this team, so it was not theirs to move.
            if (!string.Equals(previousRole, nextRole, StringComparison.Ordinal))
            {
                object? owner = await database.ScalarAsync(
                    "SELECT count(*) FROM team_members WHERE team_id = @t AND employee_id = @e AND role = 'owner'",
                    ("t", id), ("e", user.Id)).ConfigureAwait(false);
                if (Convert.ToInt32(owner, CultureInfo.InvariantCulture) == 0 && !user.IsAdministrator)
                {
                    Telemetry.Current.Signal(
                        Signals.MembershipOverpost,
                        payload: role,
                        detail: "membership " + id.ToString(CultureInfo.InvariantCulture) + "/"
                            + memberId.ToString(CultureInfo.InvariantCulture) + " moved from '" + previousRole
                            + "' to '" + nextRole + "' on a save by employee "
                            + user.Id.ToString(CultureInfo.InvariantCulture) + ", who does not own that team");
                }
            }

            return Results.Json(new { team = id, member = memberId, role = nextRole });
        });
    }

    // ------------------------------------------------------------------- reports

    private static void MapReports(WebApplication app)
    {
        app.MapGet("/api/reports/summary", async (HttpContext context) =>
        {
            PortalUser? user = await UserAsync(context).ConfigureAwait(false);
            if (user is null)
            {
                return Unauthorised();
            }

            List<Dictionary<string, object?>> rows = await Db(context).QueryAsync(
                "SELECT cost_centre, sum(hours) AS hours FROM timesheets WHERE cost_centre = @cc"
                + " GROUP BY cost_centre", ("cc", user.CostCentre)).ConfigureAwait(false);
            return Results.Json(rows);
        });

        app.MapGet("/api/reports/costs", async (HttpContext context) =>
        {
            PortalUser? user = await UserAsync(context).ConfigureAwait(false);
            if (user is null)
            {
                return Unauthorised();
            }

            List<Dictionary<string, object?>> rows = await Db(context).QueryAsync(
                "SELECT week, sum(hours) AS hours FROM timesheets WHERE cost_centre = @cc"
                + " GROUP BY week ORDER BY week", ("cc", user.CostCentre)).ConfigureAwait(false);
            return Results.Json(rows);
        });

        // ---- the streamed export -----------------------------------------------------
        // Streaming was added when the export outgrew the response buffer. Rows go out a
        // page at a time and the grouping key is applied to each page as it is written.
        app.MapGet("/api/reports/timesheets", async (HttpContext context) =>
        {
            PortalUser? user = await UserAsync(context).ConfigureAwait(false);
            if (user is null)
            {
                return Unauthorised();
            }

            string group = context.Request.Query["group"].ToString();
            if (group.Length == 0)
            {
                group = "week";
            }

            List<Dictionary<string, object?>> rows = await Db(context).QueryAsync(
                "SELECT t.id, t.employee_id, t.cost_centre, t.week, t.hours, e.display_name"
                + " FROM timesheets t JOIN employees e ON e.id = t.employee_id"
                + " ORDER BY t.week, t.cost_centre, t.id").ConfigureAwait(false);

            context.Response.ContentType = "application/json";
            await context.Response.WriteAsync("{\"group\":" + JsonSerializer.Serialize(group) + ",\"rows\":[")
                .ConfigureAwait(false);

            int written = 0;
            int foreignRows = 0;
            List<Dictionary<string, object?>> page = new();
            try
            {
                foreach (Dictionary<string, object?> row in rows)
                {
                    string separator = written == 0 ? string.Empty : ",";
                    await context.Response.WriteAsync(separator + JsonSerializer.Serialize(row))
                        .ConfigureAwait(false);
                    written++;
                    if (!string.Equals(
                            Convert.ToString(row["cost_centre"], CultureInfo.InvariantCulture),
                            user.CostCentre,
                            StringComparison.Ordinal))
                    {
                        foreignRows++;
                    }

                    page.Add(row);
                    if (page.Count < 25)
                    {
                        continue;
                    }

                    await context.Response.Body.FlushAsync(context.RequestAborted).ConfigureAwait(false);
                    GroupPage(page, group);
                    page.Clear();
                }

                GroupPage(page, group);
                await context.Response.WriteAsync("]}").ConfigureAwait(false);
            }
            catch (Exception error)
            {
                // Counted before the fault is allowed to continue, and only when rows
                // that belong to other cost centres had already left the process: at that
                // point the caller keeps whatever was flushed, whatever happens next.
                if (context.Response.HasStarted && foreignRows > 0)
                {
                    Telemetry.Current.Signal(
                        Signals.TimesheetPartial,
                        payload: group,
                        detail: "a " + error.GetType().Name + " ended the export after "
                            + written.ToString(CultureInfo.InvariantCulture) + " rows had been written, "
                            + foreignRows.ToString(CultureInfo.InvariantCulture)
                            + " of them from cost centres other than " + user.CostCentre);
                }

                throw;
            }

            return Results.Empty;
        });
    }

    /// <summary>
    /// Fold one page of rows onto the requested key. Free-form on purpose: the reporting
    /// team add keys without waiting for a deployment.
    /// </summary>
    private static void GroupPage(List<Dictionary<string, object?>> page, string group)
    {
        Dictionary<string, decimal> totals = new(StringComparer.Ordinal);
        foreach (Dictionary<string, object?> row in page)
        {
            string key = group switch
            {
                "week" => Convert.ToString(row["week"], CultureInfo.InvariantCulture) ?? string.Empty,
                "cost-centre" => Convert.ToString(row["cost_centre"], CultureInfo.InvariantCulture) ?? string.Empty,
                "employee" => Convert.ToString(row["employee_id"], CultureInfo.InvariantCulture) ?? string.Empty,
                _ => Convert.ToString(row[group], CultureInfo.InvariantCulture) ?? string.Empty,
            };
            totals[key] = totals.TryGetValue(key, out decimal running)
                ? running + Convert.ToDecimal(row["hours"], CultureInfo.InvariantCulture)
                : Convert.ToDecimal(row["hours"], CultureInfo.InvariantCulture);
        }
    }

    // ------------------------------------------------------------ administration

    private static void MapAdministration(WebApplication app)
    {
        app.MapGet("/api/integrations", async (HttpContext context) =>
        {
            PortalUser? user = await UserAsync(context).ConfigureAwait(false);
            if (user is null || !user.IsAdministrator)
            {
                return Unauthorised();
            }

            return Results.Json(new[]
            {
                new { key = "partner-supply", endpoint = "https://api.partner-supply.example/health", state = "ok" },
                new { key = "payroll", endpoint = "https://feeds.payroll-bureau.example/status", state = "ok" },
                new { key = "shift-board", endpoint = "https://shifts.meridian-castings.net/health", state = "ok" },
            });
        });

        // ---- the health check ---------------------------------------------------------
        // Answers "is the partner down, or down for us", which is only answerable from
        // inside this network.
        app.MapPost("/api/integrations/probe", async (HttpContext context) =>
        {
            PortalUser? user = await UserAsync(context).ConfigureAwait(false);
            if (user is null || !user.IsAdministrator)
            {
                return Unauthorised();
            }

            string endpoint = await FieldAsync(context, "endpoint").ConfigureAwait(false);
            if (endpoint.Length == 0)
            {
                return Results.BadRequest(new { error = "an endpoint is needed" });
            }

            OutboundProbe probe = context.RequestServices.GetRequiredService<OutboundProbe>();
            ProbeResult result = await probe
                .FetchAsync(endpoint, Signals.ProbeLinkLocal, "endpoint", context.RequestAborted)
                .ConfigureAwait(false);

            return Results.Json(new
            {
                endpoint,
                reached = result.Completed,
                status = result.Status,
                content_type = result.ContentType,
                address = result.RemoteAddress,
                body = result.Body,
                error = result.Error,
            });
        });

        // ---- the staff extract ---------------------------------------------------------
        app.MapPost("/api/directory/import", async (HttpContext context) =>
        {
            PortalUser? user = await UserAsync(context).ConfigureAwait(false);
            if (user is null || !user.IsAdministrator)
            {
                return Unauthorised();
            }

            using MemoryStream buffer = new();
            await context.Request.Body.CopyToAsync(buffer, context.RequestAborted).ConfigureAwait(false);
            byte[] document = buffer.ToArray();
            if (document.Length == 0)
            {
                return Results.BadRequest(new { error = "an extract is needed" });
            }

            DirectoryImport import = context.RequestServices.GetRequiredService<DirectoryImport>();
            ImportSummary summary = await import.PreviewAsync(document, context.RequestAborted)
                .ConfigureAwait(false);
            return Results.Json(new { records = summary.Records, message = summary.Message });
        });

        app.MapGet("/api/agents", async (HttpContext context) =>
        {
            PortalUser? user = await UserAsync(context).ConfigureAwait(false);
            if (user is null || !user.IsAdministrator)
            {
                return Unauthorised();
            }

            List<Dictionary<string, object?>> rows = await Db(context).QueryAsync(
                "SELECT id, version, source_host, digest, staged FROM agent_packages ORDER BY id")
                .ConfigureAwait(false);
            return Results.Json(rows);
        });

        // ---- staging a release ----------------------------------------------------------
        app.MapPost("/api/agents/updates", async (HttpContext context) =>
        {
            PortalUser? user = await UserAsync(context).ConfigureAwait(false);
            if (user is null || !user.IsAdministrator)
            {
                return Unauthorised();
            }

            string manifest = await FieldAsync(context, "manifest_url").ConfigureAwait(false);
            if (manifest.Length == 0)
            {
                manifest = "https://" + UpdateChannel.VendorHost + "/agent/stable.json";
            }

            UpdateChannel channel = context.RequestServices.GetRequiredService<UpdateChannel>();
            StagedRelease release = await channel.StageAsync(manifest, context.RequestAborted)
                .ConfigureAwait(false);
            return Results.Json(new
            {
                staged = release.Staged,
                version = release.Version,
                source = release.SourceHost,
                digest = release.Digest,
                signed = release.Signed,
                message = release.Message,
            });
        });

        app.MapGet("/api/templates", async (HttpContext context) =>
        {
            PortalUser? user = await UserAsync(context).ConfigureAwait(false);
            if (user is null || !user.IsAdministrator)
            {
                return Unauthorised();
            }

            DocumentStore store = context.RequestServices.GetRequiredService<DocumentStore>();
            List<string> names = Directory.EnumerateFiles(store.TemplateRoot)
                .Select(Path.GetFileNameWithoutExtension)
                .Where(name => !string.IsNullOrEmpty(name))
                .Select(name => name!)
                .OrderBy(name => name, StringComparer.Ordinal)
                .ToList();
            return Results.Json(names);
        });

        app.MapGet("/api/templates/{name}", async (HttpContext context, string name) =>
        {
            PortalUser? user = await UserAsync(context).ConfigureAwait(false);
            if (user is null || !user.IsAdministrator)
            {
                return Unauthorised();
            }

            DocumentStore store = context.RequestServices.GetRequiredService<DocumentStore>();
            string path = Path.Combine(store.TemplateRoot, Path.GetFileName(name) + ".txt");
            if (!File.Exists(path))
            {
                return Results.NotFound();
            }

            return Results.Text(
                await File.ReadAllTextAsync(path, context.RequestAborted).ConfigureAwait(false),
                "text/plain");
        });

        // ---- saving a template -----------------------------------------------------------
        // The name in the address is the file name, which is what made the editor and the
        // share the operations team browse it over trivial to build.
        app.MapPut("/api/templates/{name}", async (HttpContext context, string name) =>
        {
            PortalUser? user = await UserAsync(context).ConfigureAwait(false);
            if (user is null || !user.IsAdministrator)
            {
                return Unauthorised();
            }

            using StreamReader reader = new(context.Request.Body, Encoding.UTF8);
            string body = await reader.ReadToEndAsync(context.RequestAborted).ConfigureAwait(false);

            // Template names carry spaces and the occasional slash for the site folders,
            // so the editor sends the name escaped and it is unescaped here.
            string decoded;
            try
            {
                decoded = Uri.UnescapeDataString(name);
            }
            catch (UriFormatException)
            {
                decoded = name;
            }

            DocumentStore store = context.RequestServices.GetRequiredService<DocumentStore>();
            string path = await store
                .SaveTemplateAsync(decoded.EndsWith(".txt", StringComparison.Ordinal) ? decoded : decoded + ".txt",
                    body, context.RequestAborted)
                .ConfigureAwait(false);
            return Results.Json(new { saved = Path.GetFileName(path), bytes = body.Length });
        });
    }

    // -------------------------------------------------------------------- helpers

    private static Database Db(HttpContext context)
    {
        return context.RequestServices.GetRequiredService<Database>();
    }

    private static Task<PortalUser?> UserAsync(HttpContext context)
    {
        return context.RequestServices.GetRequiredService<Sessions>().CurrentAsync(context);
    }

    private static IResult Unauthorised()
    {
        return Results.Json(new { error = "sign in to continue" }, statusCode: StatusCodes.Status401Unauthorized);
    }

    private static async Task<JsonElement?> ReadJsonAsync(HttpContext context)
    {
        try
        {
            using MemoryStream buffer = new();
            await context.Request.Body.CopyToAsync(buffer, context.RequestAborted).ConfigureAwait(false);
            if (buffer.Length == 0)
            {
                return null;
            }

            using JsonDocument document = JsonDocument.Parse(buffer.ToArray());
            return document.RootElement.Clone();
        }
        catch (Exception)
        {
            return null;
        }
    }

    /// <summary>Read one field from a JSON body or from a form, whichever arrived.</summary>
    private static async Task<string> FieldAsync(HttpContext context, string name)
    {
        if (context.Request.HasFormContentType)
        {
            IFormCollection form = await context.Request.ReadFormAsync(context.RequestAborted)
                .ConfigureAwait(false);
            return form.TryGetValue(name, out Microsoft.Extensions.Primitives.StringValues value)
                ? value.ToString()
                : string.Empty;
        }

        return Text(await ReadJsonAsync(context).ConfigureAwait(false), name);
    }

    private static string Text(JsonElement? body, string name)
    {
        if (body is null || body.Value.ValueKind != JsonValueKind.Object)
        {
            return string.Empty;
        }

        if (!body.Value.TryGetProperty(name, out JsonElement value))
        {
            return string.Empty;
        }

        return value.ValueKind switch
        {
            JsonValueKind.String => value.GetString() ?? string.Empty,
            JsonValueKind.Number => value.GetRawText(),
            JsonValueKind.True => "true",
            JsonValueKind.False => "false",
            _ => string.Empty,
        };
    }

    private static int Number(JsonElement? body, string name, int fallback)
    {
        string text = Text(body, name);
        return int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out int parsed)
            ? parsed
            : fallback;
    }

    public static string ContentTypeFor(string name)
    {
        string extension = Path.GetExtension(name).ToLowerInvariant();
        return extension switch
        {
            ".svg" => "image/svg+xml",
            ".png" => "image/png",
            ".jpg" or ".jpeg" => "image/jpeg",
            ".gif" => "image/gif",
            ".pdf" => "application/pdf",
            ".txt" => "text/plain; charset=utf-8",
            ".csv" => "text/csv",
            ".xml" => "application/xml",
            ".html" or ".htm" => "text/html; charset=utf-8",
            _ => "application/octet-stream",
        };
    }
}
