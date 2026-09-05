using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Text.Json;
using Microsoft.AspNetCore.Http;
using Xunit;

namespace Internal.Telemetry.Tests;

/// <summary>Input description: what is recorded, and what is deliberately not collapsed.</summary>
public sealed class ParamTests
{
    private static List<string> Rendered(ParamCollector collector)
    {
        return collector.Entries
            .Select(entry => entry.In + ":" + entry.Name + "=" + entry.Sample)
            .ToList();
    }

    [Fact]
    public void RepeatedNamesCarryingDifferentValuesAreBothKept()
    {
        // De-duplication is on (location, name, digest). On (location, name) alone, a
        // repeated parameter with two different values would collapse to one entry and
        // the pair that makes two identical-looking requests behave differently would
        // disappear from the record.
        ParamCollector collector = new(64);
        collector.Add("id", "query", "1042");
        collector.Add("id", "query", "1043");
        collector.Add("id", "query", "1042");

        Assert.Equal(2, collector.Entries.Count);
        Assert.Equal(new[] { "query:id=1042", "query:id=1043" }, Rendered(collector).ToArray());
    }

    [Fact]
    public void TheSameNameInTwoLocationsIsTwoObservations()
    {
        ParamCollector collector = new(64);
        collector.Add("token", "query", "abc");
        collector.Add("token", "cookie", "abc");
        Assert.Equal(2, collector.Entries.Count);
    }

    [Fact]
    public void QueryStringPollutionIsPreservedThroughTheRequestPath()
    {
        DefaultHttpContext context = new();
        context.Request.QueryString = new QueryString("?id=1042&id=1043&sort=name");

        ParamCollector collector = new(64);
        RequestAttributes.CollectQuery(collector, context.Request);

        Assert.Equal(3, collector.Entries.Count);
        Assert.Equal(2, collector.Entries.Count(entry => entry.Name == "id"));
    }

    [Fact]
    public void ADocumentIsFlattenedIntoDottedPaths()
    {
        byte[] body = Encoding.UTF8.GetBytes(
            "{\"order\":{\"lines\":[{\"sku\":\"MC-410\"},{\"sku\":\"MC-411\"}],\"note\":\"\"},\"draft\":true,\"tags\":[]}");
        ParamCollector collector = new(64);
        RequestAttributes.CollectBody(collector, body, "application/json", 16);

        List<string> names = collector.Entries.Select(entry => entry.Name).ToList();
        Assert.Contains("order.lines.0.sku", names);
        Assert.Contains("order.lines.1.sku", names);
        Assert.Contains("order.note", names);
        Assert.Contains("draft", names);

        // Empty containers stay visible as names: "the client sent this key" is worth
        // knowing even when it sent nothing inside it.
        Assert.Contains("tags", names);
        Assert.All(collector.Entries, entry => Assert.Equal("json", entry.In));
    }

    [Fact]
    public void AFormBodyIsDecodedAndRepeatsAreKept()
    {
        byte[] body = Encoding.UTF8.GetBytes("role=viewer&role=owner&name=Helen+Abassi&note=a%2Bb");
        ParamCollector collector = new(64);
        RequestAttributes.CollectBody(collector, body, "application/x-www-form-urlencoded", 16);

        List<string> rendered = Rendered(collector);
        Assert.Contains("body:role=viewer", rendered);
        Assert.Contains("body:role=owner", rendered);
        Assert.Contains("body:name=Helen Abassi", rendered);
        Assert.Contains("body:note=a+b", rendered);
    }

    [Fact]
    public void MultipartFieldNamesAndFileNamesAreBothDescribed()
    {
        string boundary = "----MeridianBoundary8812";
        StringBuilder builder = new();
        builder.Append("--").Append(boundary).Append("\r\n");
        builder.Append("Content-Disposition: form-data; name=\"title\"\r\n\r\n");
        builder.Append("Site plan\r\n");
        builder.Append("--").Append(boundary).Append("\r\n");
        builder.Append("Content-Disposition: form-data; name=\"asset\"; filename=\"plan.svg\"\r\n");
        builder.Append("Content-Type: image/svg+xml\r\n\r\n");
        builder.Append("<svg xmlns=\"http://www.w3.org/2000/svg\"></svg>\r\n");
        builder.Append("--").Append(boundary).Append("--\r\n");

        ParamCollector collector = new(64);
        RequestAttributes.CollectBody(
            collector,
            Encoding.UTF8.GetBytes(builder.ToString()),
            "multipart/form-data; boundary=" + boundary,
            16);

        List<string> names = collector.Entries.Select(entry => entry.Name).ToList();
        Assert.Contains("title", names);
        Assert.Contains("asset", names);
        Assert.Contains("asset.filename", names);
        Assert.All(collector.Entries, entry => Assert.Equal("multipart", entry.In));
    }

    [Fact]
    public void CookiesAreParsedFromTheRawHeaderIncludingOnesTheApplicationNeverReads()
    {
        DefaultHttpContext context = new();
        context.Request.Headers["Cookie"] = "sid=9f21ab; consent=\"all\"; stray";

        ParamCollector collector = new(64);
        RequestAttributes.CollectCookies(collector, context.Request);

        List<string> rendered = Rendered(collector);
        Assert.Contains("cookie:sid=9f21ab", rendered);
        Assert.Contains("cookie:consent=all", rendered);
        Assert.Contains("cookie:stray=", rendered);
    }

    [Fact]
    public void OnlyTheHeadersWorthDescribingAreDescribed()
    {
        DefaultHttpContext context = new();
        context.Request.Headers["Host"] = "portal.meridian-castings.net";
        context.Request.Headers["Accept"] = "text/html";
        context.Request.Headers["X-Requested-With"] = "XMLHttpRequest";

        ParamCollector collector = new(64);
        RequestAttributes.CollectHeaders(collector, context.Request);

        List<string> names = collector.Entries.Select(entry => entry.Name).ToList();
        Assert.Contains("host", names);
        Assert.Contains("x-requested-with", names);
        Assert.DoesNotContain("accept", names);
    }

    [Fact]
    public void ValuesAreDigestedSizedAndSampled()
    {
        ParamEntry entry = ParamCollector.Describe("q", "query", "casting");
        Assert.Equal(64, entry.ValueSha256!.Length);
        Assert.Equal(7, entry.ValueLength);
        Assert.Equal("casting", entry.Sample);

        // The digest has to be the digest of the bytes that were on the wire, so the
        // same value seen anywhere else in the estate groups with this one.
        Assert.Equal(ParamCollector.Sha256Hex(Encoding.UTF8.GetBytes("casting")), entry.ValueSha256);
    }

    [Fact]
    public void ASampleIsClippedWithoutLeavingHalfACharacterBehind()
    {
        string longValue = new('a', ParamCollector.SampleMaxChars + 50);
        Assert.Equal(ParamCollector.SampleMaxChars, ParamCollector.Truncate(longValue).Length);

        string withPair = new string('a', ParamCollector.SampleMaxChars - 1) + "\U0001F600";
        string clipped = ParamCollector.Truncate(withPair);
        Assert.Equal(ParamCollector.SampleMaxChars - 1, clipped.Length);
        Assert.False(char.IsHighSurrogate(clipped[clipped.Length - 1]));
    }

    [Fact]
    public void TheAccumulatorIsBounded()
    {
        ParamCollector collector = new(3);
        for (int i = 0; i < 10; i++)
        {
            collector.Add("f" + i, "query", i.ToString());
        }

        Assert.Equal(3, collector.Entries.Count);
        Assert.True(collector.Truncated);
    }

    [Fact]
    public void AnUnparseableDocumentIsStillRecordedWhole()
    {
        byte[] body = Encoding.UTF8.GetBytes("{\"broken\":");
        ParamCollector collector = new(8);
        RequestAttributes.CollectBody(collector, body, "application/json", 16);

        ParamEntry entry = Assert.Single(collector.Entries);
        Assert.Equal("body", entry.Name);
        Assert.Equal("raw", entry.In);
    }

    [Fact]
    public void ADocumentBodyIsSniffedWhenNoContentTypeWasDeclared()
    {
        byte[] body = Encoding.UTF8.GetBytes("{\"a\":1}");
        ParamCollector collector = new(8);
        RequestAttributes.CollectBody(collector, body, null, 16);
        Assert.Equal("json", Assert.Single(collector.Entries).In);
    }

    [Fact]
    public void JsonLeavesRenderTheWayTheyLookedOnTheWire()
    {
        using JsonDocument document = JsonDocument.Parse("{\"s\":\"x\",\"n\":1001,\"b\":true,\"z\":null}");
        JsonElement root = document.RootElement;
        Assert.Equal("x", ParamCollector.JsonLeafText(root.GetProperty("s")));
        Assert.Equal("1001", ParamCollector.JsonLeafText(root.GetProperty("n")));
        Assert.Equal("true", ParamCollector.JsonLeafText(root.GetProperty("b")));
        Assert.Equal("null", ParamCollector.JsonLeafText(root.GetProperty("z")));
    }
}
