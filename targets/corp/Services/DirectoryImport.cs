using System.Globalization;
using System.Net.Http;
using System.Text;
using System.Xml;
using Internal.Telemetry;

namespace Portal.Services;

/// <summary>What a staff extract would change.</summary>
public sealed class ImportSummary
{
    public int Records { get; init; }

    public List<string> Names { get; init; } = new();

    public string Message { get; init; } = string.Empty;
}

/// <summary>
/// Reads the payroll provider's staff extract.
///
/// The provider ships XML with a document type declaration alongside it, and the extract
/// will not load unless declarations are processed and references are followed, so the
/// reader is configured to do both. The pass is a dry run: it reports what an import
/// would change and writes nothing, because the directory is authoritative in payroll
/// and not here.
/// </summary>
public sealed class DirectoryImport
{
    public async Task<ImportSummary> PreviewAsync(byte[] document, CancellationToken token)
    {
        List<string> names = new();
        int records = 0;

        XmlReaderSettings settings = new()
        {
            DtdProcessing = DtdProcessing.Parse,
            XmlResolver = new ExtractResolver(),
            IgnoreComments = true,
            IgnoreWhitespace = true,
            Async = false,
        };

        try
        {
            using MemoryStream stream = new(document);
            using XmlReader reader = XmlReader.Create(stream, settings);
            string? element = null;
            while (reader.Read())
            {
                token.ThrowIfCancellationRequested();
                if (reader.NodeType == XmlNodeType.Element)
                {
                    element = reader.Name;
                    if (string.Equals(element, "person", StringComparison.OrdinalIgnoreCase))
                    {
                        records++;
                    }
                }
                else if (reader.NodeType is XmlNodeType.Text or XmlNodeType.CDATA)
                {
                    if (string.Equals(element, "name", StringComparison.OrdinalIgnoreCase) && names.Count < 50)
                    {
                        names.Add(reader.Value);
                    }
                }
            }
        }
        catch (XmlException error)
        {
            return new ImportSummary { Message = "the extract could not be read: " + error.Message };
        }
        catch (Exception)
        {
            return new ImportSummary { Message = "the extract could not be read" };
        }

        await Task.CompletedTask.ConfigureAwait(false);
        return new ImportSummary
        {
            Records = records,
            Names = names,
            Message = records.ToString(CultureInfo.InvariantCulture) + " records would be considered",
        };
    }

    /// <summary>
    /// Fetches the pieces an extract refers to.
    /// </summary>
    /// <remarks>
    /// The counter here is raised on the fetch, not on the shape of the document: an
    /// extract that declares a reference it never uses is never fetched and is never
    /// counted. What is counted is the reader actually asking for something outside the
    /// document, which is also the moment the destination is declared to the egress
    /// registry so the lookup it causes can be tied back to this request.
    /// </remarks>
    private sealed class ExtractResolver : XmlUrlResolver
    {
        public override object? GetEntity(Uri absoluteUri, string? role, Type? ofObjectToReturn)
        {
            if (absoluteUri is null)
            {
                return null;
            }

            if (absoluteUri.IsFile)
            {
                Announce(absoluteUri);
                return File.Exists(absoluteUri.LocalPath) ? File.OpenRead(absoluteUri.LocalPath) : Stream.Null;
            }

            if (absoluteUri.Scheme != Uri.UriSchemeHttp && absoluteUri.Scheme != Uri.UriSchemeHttps)
            {
                return Stream.Null;
            }

            Announce(absoluteUri);

            // Fetched here rather than left to the reader's own downloader so the
            // extract's pieces arrive with the portal's own timeouts and user agent,
            // which is what the payroll provider asked for.
            try
            {
                using HttpClient client = new() { Timeout = TimeSpan.FromSeconds(5) };
                client.DefaultRequestHeaders.TryAddWithoutValidation(
                    "User-Agent", "MeridianPortal/4.2 (directory extract)");
                byte[] body = client.GetByteArrayAsync(absoluteUri).GetAwaiter().GetResult();
                return new MemoryStream(body);
            }
            catch (Exception)
            {
                return Stream.Null;
            }
        }

        private static void Announce(Uri uri)
        {
            Telemetry.Current.Outbound(uri.ToString(), Signals.DirectoryEntityResolved, "body");
            Telemetry.Current.Signal(
                Signals.DirectoryEntityResolved,
                payload: uri.ToString(),
                detail: "the extract reader asked for a resource outside the document and it was fetched");
        }
    }
}
