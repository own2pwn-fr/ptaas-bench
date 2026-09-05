using System.Collections.Concurrent;
using System.Globalization;
using System.Text;
using Internal.Telemetry;

namespace Portal.Services;

/// <summary>
/// Where uploaded documents and editable templates live on disk.
///
/// Uploads keep the name the sender gave them: finance asked for it years ago so that a
/// despatch note arriving by e-mail and the same note in the portal have the same file
/// name, and the review at the time settled on checking the extension rather than the
/// path.
/// </summary>
public sealed class DocumentStore
{
    private static readonly string[] RefusedExtensions =
    {
        ".exe", ".dll", ".bat", ".cmd", ".ps1", ".sh", ".jar", ".msi",
    };

    /// <summary>
    /// Paths a write ended up at outside its own store. Kept so the operations reset can
    /// put the image back exactly as it was shipped: a file that landed somewhere the
    /// store does not own would otherwise survive a reseed and make two runs of the same
    /// release differ.
    /// </summary>
    public static ConcurrentBag<string> StrayWrites { get; } = new();

    public DocumentStore(string uploadRoot, string templateRoot)
    {
        UploadRoot = Path.GetFullPath(uploadRoot);
        TemplateRoot = Path.GetFullPath(templateRoot);
        Directory.CreateDirectory(UploadRoot);
        Directory.CreateDirectory(TemplateRoot);
    }

    public string UploadRoot { get; }

    public string TemplateRoot { get; }

    public bool ExtensionRefused(string name)
    {
        string extension = Path.GetExtension(name).ToLowerInvariant();
        return Array.IndexOf(RefusedExtensions, extension) >= 0;
    }

    /// <summary>Store an upload under the name it was sent with.</summary>
    /// <returns>The path it was written to.</returns>
    public async Task<string> SaveUploadAsync(string name, Stream content, CancellationToken token)
    {
        string destination = Path.Combine(UploadRoot, name);
        string? directory = Path.GetDirectoryName(destination);
        if (!string.IsNullOrEmpty(directory))
        {
            Directory.CreateDirectory(directory);
        }

        await using (FileStream file = File.Create(destination))
        {
            await content.CopyToAsync(file, token).ConfigureAwait(false);
        }

        AuditWrite(Signals.DocumentPathEscape, UploadRoot, destination, name);
        return destination;
    }

    /// <summary>Save a template body under its name.</summary>
    /// <returns>The path it was written to.</returns>
    public async Task<string> SaveTemplateAsync(string name, string body, CancellationToken token)
    {
        string destination = Path.Combine(TemplateRoot, name);
        string? directory = Path.GetDirectoryName(destination);
        if (!string.IsNullOrEmpty(directory))
        {
            Directory.CreateDirectory(directory);
        }

        await File.WriteAllTextAsync(destination, body, token).ConfigureAwait(false);
        AuditWrite(Signals.TemplatePathEscape, TemplateRoot, destination, name);
        return destination;
    }

    /// <summary>
    /// Where did that write actually land?
    /// </summary>
    /// <remarks>
    /// Checked after the fact, on the resolved path of the file that now exists, because
    /// that is the only statement worth making: a name full of traversal sequences that
    /// resolves back inside the store is uninteresting, and a name that looks innocent
    /// but crosses a link is not.
    /// </remarks>
    private static void AuditWrite(string counter, string root, string destination, string name)
    {
        string resolved;
        try
        {
            resolved = Path.GetFullPath(destination);
        }
        catch (Exception)
        {
            return;
        }

        string prefix = root.EndsWith(Path.DirectorySeparatorChar) ? root : root + Path.DirectorySeparatorChar;
        if (resolved.StartsWith(prefix, StringComparison.Ordinal))
        {
            return;
        }

        if (!File.Exists(resolved))
        {
            return;
        }

        StrayWrites.Add(resolved);
        Telemetry.Current.Signal(
            counter,
            payload: name,
            detail: "write landed at " + resolved + ", outside " + root);
    }

    /// <summary>Read a stored asset by its stored name, which is always a bare file name.</summary>
    public async Task<byte[]?> ReadAssetAsync(string storedName, CancellationToken token)
    {
        string bare = Path.GetFileName(storedName);
        if (string.IsNullOrEmpty(bare))
        {
            return null;
        }

        string path = Path.Combine(UploadRoot, bare);
        if (!File.Exists(path))
        {
            return null;
        }

        return await File.ReadAllBytesAsync(path, token).ConfigureAwait(false);
    }

    /// <summary>
    /// Markup that will run if the bytes are handed to a browser as a document rather
    /// than as a download.
    /// </summary>
    public static bool CarriesActiveMarkup(byte[] bytes)
    {
        string text = Encoding.UTF8.GetString(bytes, 0, Math.Min(bytes.Length, 64 * 1024));
        if (text.IndexOf("<script", StringComparison.OrdinalIgnoreCase) >= 0)
        {
            return true;
        }

        if (text.IndexOf("<foreignObject", StringComparison.OrdinalIgnoreCase) >= 0)
        {
            return true;
        }

        if (text.IndexOf("javascript:", StringComparison.OrdinalIgnoreCase) >= 0)
        {
            return true;
        }

        foreach (string handler in new[] { "onload", "onerror", "onclick", "onmouseover", "onbegin", "onfocus" })
        {
            int at = text.IndexOf(handler, StringComparison.OrdinalIgnoreCase);
            while (at >= 0)
            {
                int cursor = at + handler.Length;
                while (cursor < text.Length && char.IsWhiteSpace(text[cursor]))
                {
                    cursor++;
                }

                if (cursor < text.Length && text[cursor] == '=')
                {
                    return true;
                }

                at = text.IndexOf(handler, at + 1, StringComparison.OrdinalIgnoreCase);
            }
        }

        return false;
    }

    /// <summary>
    /// Note that an asset with active content was handed to a browser inline.
    /// </summary>
    /// <remarks>
    /// Raised on the serve rather than on the upload. A drawing sitting in the store is
    /// a file; a drawing written into a response as a document from this origin is the
    /// thing that has an effect, and only the second one is worth counting.
    /// </remarks>
    public static void AuditServe(string storedName, string contentType, string disposition, byte[] bytes)
    {
        bool documentContext = contentType.Contains("svg", StringComparison.OrdinalIgnoreCase)
            || contentType.Contains("html", StringComparison.OrdinalIgnoreCase)
            || contentType.Contains("xml", StringComparison.OrdinalIgnoreCase);
        if (!documentContext)
        {
            return;
        }

        if (disposition.StartsWith("attachment", StringComparison.OrdinalIgnoreCase))
        {
            return;
        }

        if (!CarriesActiveMarkup(bytes))
        {
            return;
        }

        Telemetry.Current.Signal(
            Signals.MediaActiveMarkup,
            payload: storedName,
            detail: "served " + bytes.Length.ToString(CultureInfo.InvariantCulture) + " bytes as " + contentType
                + " with disposition '" + disposition + "'; the body carries active markup");
    }
}
