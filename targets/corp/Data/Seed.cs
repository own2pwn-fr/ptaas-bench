using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using Npgsql;

namespace Portal.Data;

/// <summary>
/// Everything derived from DEPLOY_SEED.
///
/// The portal is deployed from a public source tree, so the content of an instance must
/// not be the content of the tree. Anything a visitor can read - document titles, team
/// names, nicknames, share tokens, the keys the badge and the share link are built with -
/// is derived here from the deployment seed, so two instances of the same release share
/// no strings. Logins and passwords are the exception: the service desk, the release
/// checks and the identity provider all hold them, so they are stable by design.
/// </summary>
public sealed class DeploymentSeed
{
    private readonly byte[] _root;

    public DeploymentSeed(string? seed)
    {
        string material = string.IsNullOrWhiteSpace(seed) ? "meridian-portal-default" : seed.Trim();
        _root = SHA256.HashData(Encoding.UTF8.GetBytes(material));
    }

    /// <summary>A stable 32-byte key for a named purpose.</summary>
    public byte[] Key(string purpose)
    {
        using HMACSHA256 mac = new(_root);
        return mac.ComputeHash(Encoding.UTF8.GetBytes(purpose));
    }

    /// <summary>A stable key of a chosen length.</summary>
    public byte[] Key(string purpose, int length)
    {
        byte[] full = Key(purpose);
        if (length <= full.Length)
        {
            return full.AsSpan(0, length).ToArray();
        }

        byte[] output = new byte[length];
        int written = 0;
        int counter = 0;
        while (written < length)
        {
            byte[] block = Key(purpose + ":" + counter.ToString(CultureInfo.InvariantCulture));
            int take = Math.Min(block.Length, length - written);
            Array.Copy(block, 0, output, written, take);
            written += take;
            counter++;
        }

        return output;
    }

    /// <summary>A stable lower-case hex string for a named purpose.</summary>
    public string Text(string purpose, int characters)
    {
        string hex = Convert.ToHexString(Key(purpose)).ToLowerInvariant();
        return characters >= hex.Length ? hex : hex.Substring(0, characters);
    }

    /// <summary>A deterministic sequence, so two resets of one instance are identical.</summary>
    public Random Sequence(string purpose)
    {
        byte[] key = Key(purpose);
        return new Random(BitConverter.ToInt32(key, 0));
    }
}

/// <summary>Creates the schema and writes the seeded content.</summary>
public static class Seed
{
    public const int EmployeeAlice = 4102;
    public const int EmployeeOther = 4118;
    public const int EmployeeAdmin = 1004;

    private static readonly string[] Forenames =
    {
        "Helen", "Tomas", "Sofia", "Ruth", "Callum", "Iman", "Piotr", "Grace", "Daniel", "Aoife",
        "Marek", "Yusuf", "Clare", "Owen", "Nadia", "Stefan", "Bethan", "Rafal", "Louise", "Hamid",
        "Ewa", "Neil", "Carys", "Adam",
    };

    private static readonly string[] Surnames =
    {
        "Abassi", "Novak", "Durand", "Whitfield", "Doherty", "Kaur", "Zielinski", "Okafor", "Reeve",
        "Brennan", "Lis", "Demir", "Hollis", "Pryce", "Rahimi", "Grzyb", "Meredith", "Baran",
        "Fairhurst", "Salehi", "Wojcik", "Ashby", "Emlyn", "Stroud",
    };

    private static readonly string[] SiteNames = { "sheffield", "wrexham", "poznan" };

    private static readonly string[] CostCentres = { "CC-1000", "CC-4100", "CC-4200", "CC-5300", "CC-6100" };

    private static readonly string[] DocumentSubjects =
    {
        "Pattern shop layout", "Furnace maintenance record", "Despatch note template",
        "Sand reclamation report", "Heat treatment schedule", "Dimensional inspection sheet",
        "Supplier corrective action", "Tooling handover", "Shift handover summary",
        "Spectrometer calibration", "Moulding line downtime", "Scrap analysis",
        "Site induction pack", "Fettling cell rota", "Pouring temperature log",
        "Core box drawing", "Yield improvement study", "Weld repair procedure",
        "Non-conformance summary", "Annual energy return",
    };

    private static readonly string[] Headlines =
    {
        "Wrexham line completes recertification",
        "Apprentice intake doubles for the autumn term",
        "Sand reclamation upgrade cuts landfill by a third",
        "New spectrometer commissioned at Sheffield",
        "Poznan site adds a second heat treatment cell",
        "Supplier day returns to Attercliffe in March",
        "Energy contract moves to a fixed tariff",
        "Quality manual reissued at revision 14",
        "Despatch times improve after layout change",
        "Long service awards presented at Sheffield",
        "Tooling store move completes ahead of plan",
        "Graduate scheme opens for applications",
    };

    public static async Task ApplyAsync(Database database, DeploymentSeed seed)
    {
        await using NpgsqlConnection connection = await database.OpenAsync().ConfigureAwait(false);
        await using (NpgsqlCommand schema = new(SchemaSql, connection))
        {
            await schema.ExecuteNonQueryAsync().ConfigureAwait(false);
        }

        Random names = seed.Sequence("content:names");
        Random content = seed.Sequence("content:documents");

        // ---- employees -------------------------------------------------------------
        for (int i = 0; i < 24; i++)
        {
            int id = 4100 + i;
            string forename = Forenames[i % Forenames.Length];
            string surname = Surnames[(i * 7 + 3) % Surnames.Length];
            string email = char.ToLowerInvariant(forename[0]) + "." + surname.ToLowerInvariant()
                + "@meridian-castings.net";
            string nickname = surname.ToLowerInvariant() + names.Next(10, 99).ToString(CultureInfo.InvariantCulture);
            await InsertEmployee(
                connection,
                id,
                email,
                forename + " " + surname,
                nickname,
                "0114 496 " + (2200 + i).ToString(CultureInfo.InvariantCulture),
                SiteNames[i % SiteNames.Length],
                CostCentres[1 + (i % 4)],
                2500 + (i * 250),
                "member",
                seed.Text("password:" + id, 24)).ConfigureAwait(false);
        }

        // The three accounts the service desk and the release checks hold. Their logins
        // are stable across deployments; everything else about them is not.
        await InsertEmployee(
            connection, EmployeeAlice, "h.abassi@meridian-castings.net", "Helen Abassi",
            "abassi" + seed.Text("nick:4102", 4), "0114 496 2210", "sheffield", "CC-4100", 5000,
            "member", "winter-forge-3318").ConfigureAwait(false);
        await InsertEmployee(
            connection, EmployeeOther, "t.novak@meridian-castings.net", "Tomas Novak",
            "novak" + seed.Text("nick:4118", 4), "0114 496 2266", "wrexham", "CC-4200", 5000,
            "member", "copper-anvil-7741").ConfigureAwait(false);
        await InsertEmployee(
            connection, EmployeeAdmin, "s.durand@meridian-castings.net", "Sofia Durand",
            "durand" + seed.Text("nick:1004", 4), "0114 496 2001", "sheffield", "CC-1000", 250000,
            "administrator", "Foundry-Lane!204").ConfigureAwait(false);

        // ---- teams -----------------------------------------------------------------
        string[] teamNames =
        {
            "Melt and pour", "Pattern and tooling", "Quality assurance",
            "Despatch and logistics", "Facilities", "Continuous improvement",
        };
        for (int i = 0; i < teamNames.Length; i++)
        {
            await Execute(
                connection,
                "INSERT INTO teams (id, name, cost_centre) VALUES (@id, @name, @cc)",
                ("id", 11 + i),
                ("name", teamNames[i]),
                ("cc", CostCentres[i % CostCentres.Length])).ConfigureAwait(false);
        }

        for (int i = 0; i < 24; i++)
        {
            await Execute(
                connection,
                "INSERT INTO team_members (team_id, employee_id, job_title, role) VALUES (@t, @e, @j, @r)",
                ("t", 11 + (i % 6)),
                ("e", 4100 + i),
                ("j", i % 3 == 0 ? "Process engineer" : i % 3 == 1 ? "Technician" : "Planner"),
                ("r", i % 6 == 0 ? "owner" : "member")).ConfigureAwait(false);
        }

        await Execute(
            connection,
            "INSERT INTO team_members (team_id, employee_id, job_title, role) VALUES (@t, @e, @j, @r)",
            ("t", 12), ("e", EmployeeAdmin), ("j", "Head of operations"), ("r", "owner")).ConfigureAwait(false);

        // ---- documents -------------------------------------------------------------
        for (int i = 0; i < 40; i++)
        {
            string subject = DocumentSubjects[i % DocumentSubjects.Length];
            string tag = seed.Text("document:" + i, 8);
            await Execute(
                connection,
                "INSERT INTO documents (id, title, stored_name, content_type, owner_id, cost_centre, created_at)"
                + " VALUES (@id, @title, @stored, @ct, @owner, @cc, @at)",
                ("id", 8801 + i),
                ("title", subject + " " + (2024 + (i % 3)).ToString(CultureInfo.InvariantCulture)),
                ("stored", tag + "-" + subject.ToLowerInvariant().Replace(' ', '-') + ".pdf"),
                ("ct", "application/pdf"),
                ("owner", 4100 + (i % 24)),
                ("cc", CostCentres[1 + (i % 4)]),
                ("at", new DateTime(2026, 1, 1, 9, 0, 0, DateTimeKind.Utc).AddDays(i * 3))).ConfigureAwait(false);
        }

        // ---- approvals -------------------------------------------------------------
        for (int i = 0; i < 30; i++)
        {
            await Execute(
                connection,
                "INSERT INTO approvals (id, reference, requested_by, amount, state)"
                + " VALUES (@id, @ref, @by, @amount, @state)",
                ("id", 8841 + i),
                ("ref", "PR-" + (20260000 + (i * 13)).ToString(CultureInfo.InvariantCulture)),
                ("by", 4100 + (i % 24)),
                ("amount", 250 + (content.Next(1, 400) * 25)),
                ("state", i % 5 == 0 ? "approved" : "pending")).ConfigureAwait(false);
        }

        // ---- timesheets ------------------------------------------------------------
        int timesheetId = 1;
        for (int week = 1; week <= 9; week++)
        {
            for (int i = 0; i < 24; i++)
            {
                await Execute(
                    connection,
                    "INSERT INTO timesheets (id, employee_id, cost_centre, week, hours)"
                    + " VALUES (@id, @e, @cc, @w, @h)",
                    ("id", timesheetId++),
                    ("e", 4100 + i),
                    ("cc", CostCentres[1 + (i % 4)]),
                    ("w", "2026-W" + week.ToString("00", CultureInfo.InvariantCulture)),
                    ("h", 30 + (i % 12))).ConfigureAwait(false);
            }
        }

        // ---- newsroom --------------------------------------------------------------
        for (int i = 0; i < Headlines.Length; i++)
        {
            await Execute(
                connection,
                "INSERT INTO news (id, headline, body, published) VALUES (@id, @h, @b, @p)",
                ("id", 1 + i),
                ("h", Headlines[i]),
                ("b", "Reported by the communications desk at Riverside Way. Reference "
                    + seed.Text("news:" + i, 6) + "."),
                ("p", new DateOnly(2026, 1, 6).AddDays(i * 11))).ConfigureAwait(false);
        }

        // ---- connected applications ------------------------------------------------
        await Execute(
            connection,
            "INSERT INTO oauth_clients (id, name, redirect_uris) VALUES (@id, @n, @r)",
            ("id", "equipment-desk"),
            ("n", "Equipment desk"),
            ("r", "https://portal.meridian-castings.net/connect/callback"
                + " https://portal.meridian-castings.net/help/equipment")).ConfigureAwait(false);
        await Execute(
            connection,
            "INSERT INTO oauth_clients (id, name, redirect_uris) VALUES (@id, @n, @r)",
            ("id", "shift-board"),
            ("n", "Shift board"),
            ("r", "https://shifts.meridian-castings.net/oauth/callback")).ConfigureAwait(false);

        // ---- desktop agent releases -------------------------------------------------
        for (int i = 0; i < 3; i++)
        {
            await Execute(
                connection,
                "INSERT INTO agent_packages (id, version, source_host, digest, signature, staged)"
                + " VALUES (@id, @v, @h, @d, @s, @st)",
                ("id", 1 + i),
                ("v", "2.4." + i.ToString(CultureInfo.InvariantCulture)),
                ("h", "updates.meridian-castings.net"),
                ("d", seed.Text("package:" + i, 64)),
                ("s", seed.Text("signature:" + i, 96)),
                ("st", i == 2)).ConfigureAwait(false);
        }
    }

    private static async Task InsertEmployee(
        NpgsqlConnection connection,
        int id,
        string email,
        string displayName,
        string nickname,
        string telephone,
        string site,
        string costCentre,
        int approvalLimit,
        string directoryRole,
        string password)
    {
        await Execute(
            connection,
            "INSERT INTO employees (id, email, password_hash, display_name, nickname, telephone, site,"
            + " cost_centre, approval_limit, directory_role, active)"
            + " VALUES (@id, @email, @hash, @name, @nick, @tel, @site, @cc, @limit, @role, TRUE)"
            + " ON CONFLICT (id) DO UPDATE SET email = EXCLUDED.email, password_hash = EXCLUDED.password_hash,"
            + " display_name = EXCLUDED.display_name, nickname = EXCLUDED.nickname,"
            + " telephone = EXCLUDED.telephone, site = EXCLUDED.site, cost_centre = EXCLUDED.cost_centre,"
            + " approval_limit = EXCLUDED.approval_limit, directory_role = EXCLUDED.directory_role,"
            + " active = TRUE",
            ("id", id),
            ("email", email),
            ("hash", Passwords.Hash(password, id)),
            ("name", displayName),
            ("nick", nickname),
            ("tel", telephone),
            ("site", site),
            ("cc", costCentre),
            ("limit", approvalLimit),
            ("role", directoryRole)).ConfigureAwait(false);
    }

    private static async Task Execute(
        NpgsqlConnection connection,
        string sql,
        params (string Name, object? Value)[] parameters)
    {
        await using NpgsqlCommand command = new(sql, connection);
        Database.Bind(command, parameters);
        await command.ExecuteNonQueryAsync().ConfigureAwait(false);
    }

    /// <summary>
    /// Dropped and rebuilt rather than truncated: a column added by a release that has
    /// since been rolled back is exactly the kind of leftover that makes two instances
    /// of the same version behave differently.
    /// </summary>
    public const string SchemaSql = @"
DROP TABLE IF EXISTS sessions, oauth_codes, oauth_clients, workspace_layouts, timesheets,
    approvals, documents, team_members, teams, news, agent_packages, employees CASCADE;

CREATE TABLE employees (
    id integer PRIMARY KEY,
    email text UNIQUE NOT NULL,
    password_hash text NOT NULL,
    display_name text NOT NULL,
    nickname text NOT NULL DEFAULT '',
    telephone text NOT NULL DEFAULT '',
    site text NOT NULL DEFAULT 'sheffield',
    cost_centre text NOT NULL DEFAULT 'CC-4100',
    approval_limit numeric NOT NULL DEFAULT 0,
    directory_role text NOT NULL DEFAULT 'member',
    active boolean NOT NULL DEFAULT TRUE
);

CREATE TABLE teams (
    id integer PRIMARY KEY,
    name text NOT NULL,
    cost_centre text NOT NULL
);

CREATE TABLE team_members (
    team_id integer NOT NULL REFERENCES teams(id),
    employee_id integer NOT NULL REFERENCES employees(id),
    job_title text NOT NULL DEFAULT '',
    role text NOT NULL DEFAULT 'member',
    PRIMARY KEY (team_id, employee_id)
);

CREATE TABLE documents (
    id integer PRIMARY KEY,
    title text NOT NULL,
    stored_name text NOT NULL,
    content_type text NOT NULL,
    owner_id integer NOT NULL REFERENCES employees(id),
    cost_centre text NOT NULL,
    created_at timestamptz NOT NULL
);

CREATE TABLE approvals (
    id integer PRIMARY KEY,
    reference text NOT NULL,
    requested_by integer NOT NULL REFERENCES employees(id),
    amount numeric NOT NULL,
    state text NOT NULL
);

CREATE TABLE timesheets (
    id integer PRIMARY KEY,
    employee_id integer NOT NULL REFERENCES employees(id),
    cost_centre text NOT NULL,
    week text NOT NULL,
    hours numeric NOT NULL
);

CREATE TABLE news (
    id integer PRIMARY KEY,
    headline text NOT NULL,
    body text NOT NULL,
    published date NOT NULL
);

CREATE TABLE oauth_clients (
    id text PRIMARY KEY,
    name text NOT NULL,
    redirect_uris text NOT NULL
);

CREATE TABLE oauth_codes (
    code text PRIMARY KEY,
    client_id text NOT NULL,
    employee_id integer NOT NULL,
    redirect_uri text NOT NULL,
    issued_at timestamptz NOT NULL
);

CREATE TABLE agent_packages (
    id integer PRIMARY KEY,
    version text NOT NULL,
    source_host text NOT NULL,
    digest text NOT NULL,
    signature text NOT NULL DEFAULT '',
    staged boolean NOT NULL DEFAULT FALSE
);

CREATE TABLE sessions (
    id text PRIMARY KEY,
    employee_id integer NOT NULL REFERENCES employees(id),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE workspace_layouts (
    employee_id integer PRIMARY KEY REFERENCES employees(id),
    layout text NOT NULL
);
";
}

/// <summary>Password storage. Derivation cost is deliberate; nothing else here is.</summary>
public static class Passwords
{
    private const int Iterations = 120_000;

    public static string Hash(string password, int employeeId)
    {
        byte[] salt = SHA256.HashData(
            Encoding.UTF8.GetBytes("meridian:" + employeeId.ToString(CultureInfo.InvariantCulture)))
            .AsSpan(0, 16).ToArray();
        byte[] derived = Rfc2898DeriveBytes.Pbkdf2(
            Encoding.UTF8.GetBytes(password), salt, Iterations, HashAlgorithmName.SHA256, 32);
        return "pbkdf2$" + Iterations.ToString(CultureInfo.InvariantCulture) + "$"
            + Convert.ToBase64String(salt) + "$" + Convert.ToBase64String(derived);
    }

    public static bool Verify(string password, string stored)
    {
        string[] parts = stored.Split('$');
        if (parts.Length != 4 || parts[0] != "pbkdf2")
        {
            return false;
        }

        if (!int.TryParse(parts[1], NumberStyles.Integer, CultureInfo.InvariantCulture, out int iterations))
        {
            return false;
        }

        byte[] salt;
        byte[] expected;
        try
        {
            salt = Convert.FromBase64String(parts[2]);
            expected = Convert.FromBase64String(parts[3]);
        }
        catch (FormatException)
        {
            return false;
        }

        byte[] derived = Rfc2898DeriveBytes.Pbkdf2(
            Encoding.UTF8.GetBytes(password), salt, iterations, HashAlgorithmName.SHA256, expected.Length);
        return CryptographicOperations.FixedTimeEquals(derived, expected);
    }
}
