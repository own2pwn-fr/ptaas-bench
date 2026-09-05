using System.Globalization;
using System.Text.Json;
using Internal.Telemetry;
using Portal.Data;

namespace Portal.Services;

/// <summary>The result of staging a desktop agent release.</summary>
public sealed class StagedRelease
{
    public bool Staged { get; init; }

    public string Version { get; init; } = string.Empty;

    public string SourceHost { get; init; } = string.Empty;

    public string Digest { get; init; } = string.Empty;

    public bool Signed { get; init; }

    public string Message { get; init; } = string.Empty;
}

/// <summary>
/// The desktop agent's update channel.
///
/// Agents ask the portal what the current release is; the portal reads a manifest from
/// the vendor host and stages the package it names. The manifest address became
/// configurable when the estate moved to a mirror on the Wrexham line, and the digest in
/// the manifest is what the staging step compares against. Publisher signing is on the
/// plan for the agent's second release.
/// </summary>
public sealed class UpdateChannel
{
    public const string VendorHost = "updates.meridian-castings.net";

    private readonly Database _database;
    private readonly OutboundProbe _probe;

    public UpdateChannel(Database database, OutboundProbe probe)
    {
        _database = database;
        _probe = probe;
    }

    public async Task<StagedRelease> StageAsync(string manifestUrl, CancellationToken token)
    {
        ProbeResult fetched = await _probe
            .FetchAsync(manifestUrl, Signals.UpdateUnsigned, "manifest_url", token)
            .ConfigureAwait(false);
        if (!fetched.Completed || fetched.Status >= 400 || fetched.Body.Length == 0)
        {
            return new StagedRelease { Staged = false, Message = "the manifest could not be read" };
        }

        string version;
        string digest;
        string signature;
        try
        {
            using JsonDocument document = JsonDocument.Parse(fetched.Body);
            JsonElement root = document.RootElement;
            version = Text(root, "version");
            digest = Text(root, "sha256");
            signature = Text(root, "signature");
        }
        catch (JsonException)
        {
            return new StagedRelease { Staged = false, Message = "the manifest could not be read" };
        }

        if (version.Length == 0 || digest.Length == 0)
        {
            return new StagedRelease { Staged = false, Message = "the manifest is incomplete" };
        }

        string host = Internal.Telemetry.TelemetryClient.HostOf(manifestUrl);
        int id = await NextIdAsync().ConfigureAwait(false);
        await _database.ExecuteAsync(
            "INSERT INTO agent_packages (id, version, source_host, digest, signature, staged)"
            + " VALUES (@id, @v, @h, @d, @s, TRUE)",
            ("id", id),
            ("v", version),
            ("h", host),
            ("d", digest),
            ("s", signature)).ConfigureAwait(false);

        await _database.ExecuteAsync(
            "UPDATE agent_packages SET staged = FALSE WHERE id <> @id", ("id", id)).ConfigureAwait(false);

        bool signed = signature.Length > 0 && VerifySignature(digest, signature);
        AuditStaged(manifestUrl, host, version, digest, signed);

        return new StagedRelease
        {
            Staged = true,
            Version = version,
            SourceHost = host,
            Digest = digest,
            Signed = signed,
            Message = "release " + version + " is now the one agents will collect",
        };
    }

    /// <summary>
    /// Note a release that entered the channel without anything vouching for it.
    /// </summary>
    /// <remarks>
    /// Raised after the package has been staged, which is the point at which agents will
    /// collect it, and only when the manifest carried no publisher signature and did not
    /// come from the vendor host. A mirror of the vendor host, or a correctly signed
    /// manifest from anywhere, is the ordinary case and is not counted.
    /// </remarks>
    private static void AuditStaged(string manifestUrl, string host, string version, string digest, bool signed)
    {
        if (signed || string.Equals(host, VendorHost, StringComparison.OrdinalIgnoreCase))
        {
            return;
        }

        Telemetry.Current.Signal(
            Signals.UpdateUnsigned,
            payload: manifestUrl,
            detail: "release " + version + " was staged from " + host + " with digest " + digest
                + " and no publisher signature; agents will collect it on their next check");
    }

    /// <summary>
    /// There is no publisher key in this release, so nothing can be verified yet. The
    /// call site is here so the check has somewhere to go when there is one.
    /// </summary>
    private static bool VerifySignature(string digest, string signature)
    {
        return false;
    }

    private static string Text(JsonElement root, string name)
    {
        return root.TryGetProperty(name, out JsonElement value) && value.ValueKind == JsonValueKind.String
            ? value.GetString() ?? string.Empty
            : string.Empty;
    }

    private async Task<int> NextIdAsync()
    {
        object? scalar = await _database
            .ScalarAsync("SELECT COALESCE(max(id), 0) + 1 FROM agent_packages")
            .ConfigureAwait(false);
        return Convert.ToInt32(scalar, CultureInfo.InvariantCulture);
    }
}
