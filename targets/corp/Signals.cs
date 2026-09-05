namespace Portal;

/// <summary>
/// Application anomaly counters.
///
/// The portal reports two kinds of thing to the observability stack: one request record
/// per request, raised by the telemetry middleware, and the counters below, which the
/// service raises when it notices something it did not expect to be able to happen.
///
/// One rule applies to every counter here, and it is why the on-call rota tolerates
/// them: raise on the confirmed effect, never on the suspicious input. A counter that
/// also counts inputs which turned out to be inert is dominated by noise within a day
/// and stops being usable as an alert, so each call site takes the observation after the
/// fact and decides from that.
/// </summary>
public static class Signals
{
    /// <summary>A saved layout produced an instance of a type outside this assembly.</summary>
    public const string LayoutTypeBinding = "corp.workspace.layout.type_binding";

    /// <summary>The cached layout cookie produced an instance of a type outside this assembly.</summary>
    public const string SessionTypeBinding = "corp.workspace.session.type_binding";

    /// <summary>A profile save changed a property outside the editable set.</summary>
    public const string BinderOverpost = "corp.binder.overpost_applied";

    /// <summary>A membership save changed a role the caller may not grant.</summary>
    public const string MembershipOverpost = "corp.teams.member.overpost_applied";

    /// <summary>An uploaded document landed outside the upload root.</summary>
    public const string DocumentPathEscape = "corp.documents.write.path_escape";

    /// <summary>A saved template landed outside the template directory.</summary>
    public const string TemplatePathEscape = "corp.templates.write.path_escape";

    /// <summary>An operation ran on a verb the caller was not admitted on.</summary>
    public const string ApprovalsOverride = "corp.approvals.method.override_applied";

    /// <summary>A profile save ran a destructive operation the caller was not admitted on.</summary>
    public const string ProfileOverride = "corp.profile.method.override_applied";

    /// <summary>The converter parsed a field the portal's client never emits.</summary>
    public const string RenderFieldInjected = "corp.render.header.field_injected";

    /// <summary>The directory import resolved an external reference.</summary>
    public const string DirectoryEntityResolved = "corp.directory.import.entity_resolved";

    /// <summary>An asset carrying active content was served inline.</summary>
    public const string MediaActiveMarkup = "corp.media.asset.active_markup_served";

    /// <summary>A probe connected to the host's instance metadata address and was answered.</summary>
    public const string ProbeLinkLocal = "corp.integrations.probe.link_local_reached";

    /// <summary>A badge granted a role the employee does not hold.</summary>
    public const string BadgeBlockSplice = "corp.badge.token.block_splice";

    /// <summary>Two different answers separated padding from integrity for one caller.</summary>
    public const string SharePaddingDistinguished = "corp.share.token.padding_distinguished";

    /// <summary>An authorisation code was delivered to a host outside the registrations.</summary>
    public const string AuthorizeForeignDelivery = "corp.connect.authorize.foreign_delivery";

    /// <summary>A diagnostic body carrying internal detail was served.</summary>
    public const string ReportsDiagnostic = "corp.reports.export.diagnostic_disclosed";

    /// <summary>Rows outside the caller's cost centres were flushed before a fault.</summary>
    public const string TimesheetPartial = "corp.reports.timesheets.partial_disclosure";

    /// <summary>A redirect left the portal's own host from the link interstitial.</summary>
    public const string LinkOffsite = "corp.links.follow.offsite_redirect";

    /// <summary>A redirect left the portal's own host from the document handoff.</summary>
    public const string HandoffOffsite = "corp.documents.handoff.offsite_redirect";

    /// <summary>An unauthenticated package entered the agent update channel.</summary>
    public const string UpdateUnsigned = "corp.agents.update.unsigned_accepted";
}
