using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using Npgsql;

namespace Portal.Data;

/// <summary>
/// The portal's data access. Small enough to be read in one sitting, which is the whole
/// reason there is no mapper here: the reporting screens want their own shapes and the
/// rest is half a dozen tables.
/// </summary>
public sealed class Database
{
    private readonly NpgsqlDataSource _source;

    public Database(NpgsqlDataSource source)
    {
        _source = source;
    }

    public NpgsqlConnection Open() => _source.OpenConnection();

    public async Task<NpgsqlConnection> OpenAsync(CancellationToken token = default)
    {
        return await _source.OpenConnectionAsync(token).ConfigureAwait(false);
    }

    public async Task ExecuteAsync(string sql, params (string Name, object? Value)[] parameters)
    {
        await using NpgsqlConnection connection = await OpenAsync().ConfigureAwait(false);
        await using NpgsqlCommand command = new(sql, connection);
        Bind(command, parameters);
        await command.ExecuteNonQueryAsync().ConfigureAwait(false);
    }

    public async Task<object?> ScalarAsync(string sql, params (string Name, object? Value)[] parameters)
    {
        await using NpgsqlConnection connection = await OpenAsync().ConfigureAwait(false);
        await using NpgsqlCommand command = new(sql, connection);
        Bind(command, parameters);
        return await command.ExecuteScalarAsync().ConfigureAwait(false);
    }

    /// <summary>Read rows as dictionaries. Used by the screens that shape their own output.</summary>
    public async Task<List<Dictionary<string, object?>>> QueryAsync(
        string sql,
        params (string Name, object? Value)[] parameters)
    {
        List<Dictionary<string, object?>> rows = new();
        await using NpgsqlConnection connection = await OpenAsync().ConfigureAwait(false);
        await using NpgsqlCommand command = new(sql, connection);
        Bind(command, parameters);
        await using NpgsqlDataReader reader = await command.ExecuteReaderAsync().ConfigureAwait(false);
        while (await reader.ReadAsync().ConfigureAwait(false))
        {
            Dictionary<string, object?> row = new(StringComparer.OrdinalIgnoreCase);
            for (int i = 0; i < reader.FieldCount; i++)
            {
                row[reader.GetName(i)] = await reader.IsDBNullAsync(i).ConfigureAwait(false)
                    ? null
                    : reader.GetValue(i);
            }

            rows.Add(row);
        }

        return rows;
    }

    public static void Bind(NpgsqlCommand command, (string Name, object? Value)[] parameters)
    {
        foreach ((string name, object? value) in parameters)
        {
            command.Parameters.AddWithValue(name, value ?? DBNull.Value);
        }
    }

    /// <summary>
    /// A one-line digest of the state that is meant to be constant between runs. It
    /// changes if and only if the seeded state changed, which is what lets the operations
    /// rota tell "the instance is clean" from "somebody left data behind".
    /// </summary>
    public async Task<string> DigestAsync(string uploadRoot, string templateRoot)
    {
        StringBuilder builder = new();
        string[] statements =
        {
            "SELECT id, email, display_name, nickname, cost_centre, approval_limit, directory_role FROM employees ORDER BY id",
            "SELECT id, name, cost_centre FROM teams ORDER BY id",
            "SELECT team_id, employee_id, job_title, role FROM team_members ORDER BY team_id, employee_id",
            "SELECT id, title, stored_name, content_type, owner_id, cost_centre FROM documents ORDER BY id",
            "SELECT id, reference, requested_by, amount, state FROM approvals ORDER BY id",
            "SELECT id, employee_id, cost_centre, week, hours FROM timesheets ORDER BY id",
            "SELECT id, headline, published FROM news ORDER BY id",
            "SELECT id, name, redirect_uris FROM oauth_clients ORDER BY id",
            "SELECT id, version, source_host, digest, signature, staged FROM agent_packages ORDER BY id",
            "SELECT count(*) FROM sessions",
            "SELECT count(*) FROM oauth_codes",
            "SELECT count(*) FROM workspace_layouts",
        };

        foreach (string sql in statements)
        {
            foreach (Dictionary<string, object?> row in await QueryAsync(sql).ConfigureAwait(false))
            {
                foreach (KeyValuePair<string, object?> cell in row)
                {
                    builder.Append(cell.Key).Append('=')
                        .Append(Convert.ToString(cell.Value, CultureInfo.InvariantCulture))
                        .Append(';');
                }

                builder.Append('\n');
            }
        }

        AppendTree(builder, uploadRoot);
        AppendTree(builder, templateRoot);

        byte[] digest = SHA256.HashData(Encoding.UTF8.GetBytes(builder.ToString()));
        return Convert.ToHexString(digest).ToLowerInvariant();
    }

    private static void AppendTree(StringBuilder builder, string root)
    {
        if (!Directory.Exists(root))
        {
            builder.Append("absent:").Append(root).Append('\n');
            return;
        }

        foreach (string path in Directory.EnumerateFiles(root, "*", SearchOption.AllDirectories)
                     .OrderBy(p => p, StringComparer.Ordinal))
        {
            FileInfo info = new(path);
            builder.Append(Path.GetRelativePath(root, path)).Append(':').Append(info.Length).Append('\n');
        }
    }
}
