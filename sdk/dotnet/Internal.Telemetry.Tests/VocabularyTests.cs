using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using Xunit;

namespace Internal.Telemetry.Tests;

/// <summary>
/// The package ships inside the target containers, so anything a reader of that
/// filesystem could find has to look like an ordinary internal observability library.
///
/// The list below is copied from targets/target-contract.yaml, section `deception`. It
/// is duplicated rather than parsed from that file so the check keeps working when this
/// project is built on its own, and because a stale copy fails loudly (a term removed
/// upstream only makes this stricter) whereas a missing file would fail open.
///
/// This is the kind of property that rots back in silently: one debugging comment, one
/// renamed symbol, and the cover is gone. So it is a test, not a review item.
/// </summary>
public sealed class VocabularyTests
{
    private static readonly string[] ForbiddenTerms =
    {
        // Exactly the contract's list.
        "bench",
        "benchmark",
        "ptaas-bench",
        "vuln",
        "vulnerable",
        "insecure",
        "deliberately",
        "ctf",
        "flag",
        "challenge",
        "lab",
        "testbed",
        "sandbox",
        "honeypot",
        "canary",
        "oracle",
        "ground truth",
        "trigger",
        "exploit-me",
        "dvwa",
        "juice-shop",
        "scanner",
        "evaluation",
        "scoring",
        // Not in the contract's list, but just as revealing in a comment.
        "grader",
        "answer key",
        "pentest",
        "attacker",
        "adversary",
        "corpus",
        "planted",
        "instrumented target",
        "under test",
    };

    /// <summary>
    /// Terms whose inflections are equally revealing, matched as prefixes. "vulnerability"
    /// and "benchmarking" are not the literal entries above, and nobody would accept them.
    /// </summary>
    private static readonly string[] ForbiddenStems =
    {
        "bench", "vuln", "exploit", "scan", "honeypot", "ctf", "dvwa", "ptaas",
    };

    /// <summary>
    /// Split text into words on punctuation AND on camel-case boundaries, so
    /// <c>benchMiddleware</c> yields two words.
    /// </summary>
    /// <remarks>
    /// Matching whole words rather than substrings is the whole point: a naive
    /// "contains" check fires on ordinary English, because "available" and "collaborator"
    /// both contain "lab", and so does "elaborate".
    /// </remarks>
    /// <param name="text">Text to split.</param>
    /// <returns>Lower-case words.</returns>
    public static List<string> Tokenize(string text)
    {
        string spaced = Regex.Replace(text ?? string.Empty, "([a-z0-9])([A-Z])", "$1 $2");
        return Regex.Split(spaced, "[^A-Za-z0-9]+")
            .Where(word => word.Length > 0)
            .Select(word => word.ToLowerInvariant())
            .ToList();
    }

    /// <summary>Every forbidden term or stem present in the text.</summary>
    /// <param name="text">Text to check.</param>
    /// <returns>Sorted hits, empty when the text is clean.</returns>
    public static List<string> FindForbidden(string text)
    {
        List<string> tokens = Tokenize(text);
        string stream = " " + string.Join(" ", tokens) + " ";
        SortedSet<string> hits = new(StringComparer.Ordinal);

        foreach (string term in ForbiddenTerms)
        {
            string phrase = string.Join(" ", Tokenize(term));
            if (phrase.Length > 0 && stream.Contains(" " + phrase + " ", StringComparison.Ordinal))
            {
                hits.Add(term);
            }
        }

        foreach (string stem in ForbiddenStems)
        {
            if (tokens.Any(token => token.StartsWith(stem, StringComparison.Ordinal)))
            {
                hits.Add(stem + "*");
            }
        }

        return hits.ToList();
    }

    private static string PackageRoot()
    {
        DirectoryInfo? directory = new(AppContext.BaseDirectory);
        while (directory is not null)
        {
            string candidate = Path.Combine(directory.FullName, "Internal.Telemetry", "Internal.Telemetry.csproj");
            if (File.Exists(candidate))
            {
                return Path.Combine(directory.FullName, "Internal.Telemetry");
            }

            directory = directory.Parent;
        }

        throw new InvalidOperationException("could not locate the package sources from " + AppContext.BaseDirectory);
    }

    private static List<string> ShippedFiles()
    {
        string root = PackageRoot();
        return Directory.EnumerateFiles(root, "*", SearchOption.AllDirectories)
            .Where(path => !path.Contains(Path.DirectorySeparatorChar + "bin" + Path.DirectorySeparatorChar, StringComparison.Ordinal))
            .Where(path => !path.Contains(Path.DirectorySeparatorChar + "obj" + Path.DirectorySeparatorChar, StringComparison.Ordinal))
            .OrderBy(path => path, StringComparer.Ordinal)
            .ToList();
    }

    [Fact]
    public void MatcherMatchesWholeWordsAndCamelCaseSegments()
    {
        Assert.Contains("bench", FindForbidden("const benchMiddleware = 1"));
        Assert.Contains("bench", FindForbidden("BENCH_APP=portal"));
        Assert.Contains("ptaas-bench", FindForbidden("this is a ptaas-bench package"));
        Assert.Contains("ground truth", FindForbidden("the ground truth for this run"));
        Assert.Contains("vuln*", FindForbidden("vulnerabilities were found"));
    }

    [Fact]
    public void MatcherIgnoresOrdinaryEnglishThatMerelyContainsATerm()
    {
        // The reason this check splits into words instead of using "contains".
        Assert.Empty(FindForbidden("no collaborator is available; elaborate labelling"));
        Assert.Empty(FindForbidden("the interface is stable and configurable"));
        Assert.Empty(FindForbidden("a label on a table in a laboratory-adjacent building"));
    }

    [Fact]
    public void MatcherErrsTowardsOverStrictnessOnStems()
    {
        // "scandinavian" starts with "scan", so it fires. That is the intended trade:
        // a false hit costs one rewritten comment, a missed one costs the cover of
        // every service the package is installed in.
        Assert.Equal(new[] { "scan*" }, FindForbidden("scandinavian").ToArray());
    }

    [Fact]
    public void TheWalkFindsANonTrivialNumberOfFiles()
    {
        List<string> files = ShippedFiles();
        Assert.True(files.Count > 8, "expected the package sources, found " + files.Count + " files");
        Assert.Contains(files, path => path.EndsWith("Internal.Telemetry.csproj", StringComparison.Ordinal));
        Assert.Contains(files, path => path.EndsWith("TelemetryClient.cs", StringComparison.Ordinal));
    }

    [Fact]
    public void NoShippedFileCarriesRevealingVocabulary()
    {
        List<string> offenders = new();
        string root = PackageRoot();
        foreach (string path in ShippedFiles())
        {
            string relative = Path.GetRelativePath(root, path);
            List<string> inName = FindForbidden(relative);
            if (inName.Count > 0)
            {
                offenders.Add(relative + " [name]: " + string.Join(", ", inName));
            }

            List<string> inBody = FindForbidden(File.ReadAllText(path));
            if (inBody.Count > 0)
            {
                offenders.Add(relative + ": " + string.Join(", ", inBody));
            }
        }

        Assert.Equal(Array.Empty<string>(), offenders.ToArray());
    }

    [Fact]
    public void NoShippedFileCarriesACatalogIdentifier()
    {
        foreach (string path in ShippedFiles())
        {
            string text = File.ReadAllText(path);
            Assert.False(
                Regex.IsMatch(text, "BENCH-[A-Z0-9]+-[0-9]{4}"),
                path + " carries an identifier from the platform");
            Assert.DoesNotContain("selftest", text, StringComparison.OrdinalIgnoreCase);
            Assert.DoesNotContain("self-test", text, StringComparison.OrdinalIgnoreCase);
        }
    }

    [Fact]
    public void OnlyTelemetryNamedEnvironmentVariablesAreRead()
    {
        string source = string.Join(
            "\n",
            ShippedFiles().Where(path => path.EndsWith(".cs", StringComparison.Ordinal)).Select(File.ReadAllText));

        SortedSet<string> names = new(StringComparer.Ordinal);
        foreach (Match match in Regex.Matches(source, "\"(TELEMETRY_[A-Z0-9_]+)\""))
        {
            names.Add(match.Groups[1].Value);
        }

        Assert.Equal(
            new[]
            {
                "TELEMETRY_BATCH_MAX",
                "TELEMETRY_CORRELATIONS_PATH",
                "TELEMETRY_ENABLED",
                "TELEMETRY_ENDPOINT",
                "TELEMETRY_EVENTS_PATH",
                "TELEMETRY_MAX_BODY_BYTES",
                "TELEMETRY_MAX_PARAMS",
                "TELEMETRY_QUEUE_MAX",
                "TELEMETRY_SERVICE",
                "TELEMETRY_SYNTHETIC_CIDRS",
                "TELEMETRY_TIMEOUT_S",
            },
            names.ToArray());

        // Any other environment name read from the shipped sources would be a second
        // vocabulary to keep clean; there is exactly one, and it is this one.
        Assert.DoesNotContain("Environment.GetEnvironmentVariable(\"BENCH", source, StringComparison.Ordinal);
    }
}
