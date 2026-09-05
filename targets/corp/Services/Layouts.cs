using System.Reflection;
using System.Text;
using Internal.Telemetry;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Newtonsoft.Json.Serialization;

namespace Portal.Services;

/// <summary>One saved workspace tile.</summary>
public sealed class LayoutTile
{
    public string Key { get; set; } = string.Empty;

    public int Width { get; set; } = 1;
}

/// <summary>A saved workspace layout.</summary>
public sealed class LayoutState
{
    public int Columns { get; set; } = 3;

    public List<LayoutTile> Tiles { get; set; } = new();
}

/// <summary>
/// Reading and writing the saved workspace layout.
///
/// The layout is written by the browser and read back both from the database and from
/// the first-paint cookie. Tile classes are contributed by two other teams and are not
/// all known to this assembly at compile time, so the writer records the class name
/// alongside the values and the reader honours it; that is what lets a contributed tile
/// round-trip instead of collapsing into a bag of properties.
/// </summary>
public static class Layouts
{
    private static readonly Assembly Own = typeof(Layouts).Assembly;

    /// <summary>Serialise a layout, keeping the class names.</summary>
    public static string Write(LayoutState state)
    {
        string document = JsonConvert.SerializeObject(state, SettingsFor(null));
        return Convert.ToBase64String(Encoding.UTF8.GetBytes(document));
    }

    /// <summary>
    /// Read a layout back.
    /// </summary>
    /// <param name="encoded">The base64 document.</param>
    /// <param name="counter">Which counter to raise if the graph turns out to be unusual.</param>
    /// <param name="source">Where the document came from, for the record.</param>
    /// <returns>Whatever the document described, or null when it could not be read.</returns>
    public static object? Read(string? encoded, string counter, string source)
    {
        if (string.IsNullOrWhiteSpace(encoded))
        {
            return null;
        }

        string document;
        try
        {
            document = Encoding.UTF8.GetString(Convert.FromBase64String(encoded.Trim()));
        }
        catch (FormatException)
        {
            return null;
        }

        RecordingBinder binder = new();
        object? restored;
        try
        {
            restored = JsonConvert.DeserializeObject(document, SettingsFor(binder));
        }
        catch (Exception)
        {
            // A document naming a class that cannot be built is a bad document, not a
            // bad request: the workspace falls back to the default arrangement. Nothing
            // is counted, because nothing was built.
            return null;
        }

        // Reached only when the read returned, which means every class the binder
        // resolved was also constructed. That is the difference between a document that
        // named something and a document that built it.
        Audit(binder, counter, encoded, source);
        return restored;
    }

    /// <summary>Coerce whatever came back into something the page can render.</summary>
    public static LayoutState AsState(object? restored)
    {
        if (restored is LayoutState state)
        {
            return state;
        }

        return Default();
    }

    /// <summary>The arrangement a workspace starts with.</summary>
    public static LayoutState Default()
    {
        return new LayoutState
        {
            Columns = 3,
            Tiles = new List<LayoutTile>
            {
                new() { Key = "approvals", Width = 1 },
                new() { Key = "documents", Width = 2 },
                new() { Key = "timesheets", Width = 1 },
            },
        };
    }

    private static JsonSerializerSettings SettingsFor(ISerializationBinder? binder)
    {
        JsonSerializerSettings settings = new()
        {
            TypeNameHandling = TypeNameHandling.All,
            MaxDepth = 24,
        };
        if (binder is not null)
        {
            settings.SerializationBinder = binder;
        }

        return settings;
    }

    private static void Audit(RecordingBinder binder, string counter, string encoded, string source)
    {
        foreach (Type type in binder.Resolved)
        {
            if (!IsForeign(type))
            {
                continue;
            }

            Telemetry.Current.Signal(
                counter,
                payload: encoded,
                detail: "restoring the layout from " + source + " built an instance of "
                    + (type.FullName ?? type.Name) + ", which is not a tile class of this application");
            return;
        }
    }

    /// <summary>
    /// A class the document chose rather than one this application defines.
    /// </summary>
    /// <remarks>
    /// The writer emits our own tile classes and the ordinary lists that hold them, so
    /// a generic whose arguments are ours is our own shape and not an unusual one. Nodes
    /// the reader creates for values that carried no class name are not a choice at all.
    /// </remarks>
    private static bool IsForeign(Type type)
    {
        if (type.Assembly == Own)
        {
            return false;
        }

        if (typeof(JToken).IsAssignableFrom(type))
        {
            return false;
        }

        if (type.IsGenericType)
        {
            foreach (Type argument in type.GetGenericArguments())
            {
                if (argument.Assembly == Own)
                {
                    return false;
                }
            }
        }

        return type != typeof(string) && !type.IsPrimitive;
    }

    /// <summary>
    /// Keeps a note of every class name the document asked for and the class it
    /// resolved to, so the workspace can report an arrangement it did not recognise.
    /// </summary>
    private sealed class RecordingBinder : ISerializationBinder
    {
        private readonly DefaultSerializationBinder _inner = new();

        public List<Type> Resolved { get; } = new();

        public Type BindToType(string? assemblyName, string typeName)
        {
            Type type = _inner.BindToType(assemblyName, typeName);
            Resolved.Add(type);
            return type;
        }

        public void BindToName(Type serializedType, out string? assemblyName, out string? typeName)
        {
            _inner.BindToName(serializedType, out assemblyName, out typeName);
        }
    }
}
