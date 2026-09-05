package com.calderwood.meridian.platform;

/**
 * Names of the counters this service raises for itself.
 *
 * <p>They are metric names, so they live in one place: a counter whose name is spelled
 * out at the call site eventually gets spelled two ways, and the second series is one
 * nobody is watching. Everything here is dotted, lower case, and named after the thing
 * observed rather than after the code that observed it.
 *
 * <p>Each of these is raised on a confirmed effect and never on the shape of an input.
 * A counter that also moves when a request merely looks unusual is noise, and noise on
 * an operational counter is how a real incident gets ignored.
 */
public final class Anomalies {

    private Anomalies() {
    }

    // --- reporting -------------------------------------------------------------
    public static final String LEDGER_STATEMENT_STALL = "console.reporting.ledger.statement_stall";
    public static final String RECOVERY_STATEMENT_STALL = "console.recovery.lookup.statement_stall";
    public static final String SUMMARY_SCOPE_OVERRIDE = "console.reports.summary.header_scope_override";

    // --- rendering -------------------------------------------------------------
    public static final String NOTICE_EXPRESSION_EVALUATED = "console.notices.render.expression_evaluated";

    // --- directory -------------------------------------------------------------
    public static final String DIRECTORY_FILTER_WIDENED = "console.directory.people.filter_widened";
    public static final String SIGN_IN_FILTER_WIDENED = "console.session.bind_filter_widened";

    // --- documents -------------------------------------------------------------
    public static final String TARIFF_NODE_ESCAPE = "console.tariffs.lookup.node_escape";
    public static final String INTAKE_ENTITY_RESOLVED = "console.intake.document.entity_resolved";
    public static final String MANIFEST_ENTITY_RESOLVED_REMOTE =
            "console.intake.manifest.entity_resolved_remote";
    public static final String EXPORT_STYLESHEET_EXTERNAL_CALL = "console.exports.stylesheet.external_call";

    // --- expressions -----------------------------------------------------------
    public static final String RULE_HOST_TYPE_REACHED = "console.rules.expression.host_type_reached";
    public static final String SEARCH_HOST_TYPE_REACHED =
            "console.search.sort_expression_host_type_reached";
    public static final String TEMPLATE_DYNAMIC_LOOKUP =
            "console.notifications.template.dynamic_lookup_resolved";

    // --- authorisation ---------------------------------------------------------
    public static final String MEMBER_RESTRICTED_FIELD = "console.members.projection.restricted_field_served";
    public static final String AUDIT_RESTRICTED_FIELD = "console.audit.expansion.restricted_field_served";
    public static final String APPROVAL_ROLE_GATE_MISSED = "console.approvals.decision.role_gate_missed";
    public static final String INVOICE_FOREIGN_SCOPE = "console.orgs.invoices.foreign_scope_served";

    // --- sessions --------------------------------------------------------------
    public static final String FACTORY_ACCOUNT_SIGNED_IN = "console.session.factory_account_signed_in";
    public static final String TOKEN_ALGORITHM_DOWNGRADED = "console.session.token_algorithm_downgraded";

    // --- operations ------------------------------------------------------------
    public static final String PROBE_OFFLIST_HOST = "console.integrations.probe.offlist_host_fetched";
    public static final String MANAGEMENT_INTERNALS_SERVED =
            "console.management.internals_served_anonymous";
    public static final String LOGFILE_CREDENTIAL_SERVED = "console.audit.logfile.credential_served";
    public static final String ARCHIVE_ENTRY_ESCAPED = "console.imports.archive.entry_escaped_root";
    public static final String BATCH_ALLOCATION_OVERRUN = "console.exports.batch_allocation_overrun";

    // --- workspace -------------------------------------------------------------
    public static final String LAYOUT_RESTORE_HOOK_RAN = "console.workspace.layout.restore_hook_ran";
    public static final String LAYOUT_COOKIE_RESTORE_RAN = "console.session.layout_cookie_restore_ran";
}
