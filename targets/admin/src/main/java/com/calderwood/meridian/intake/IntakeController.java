package com.calderwood.meridian.intake;

import com.calderwood.meridian.audit.AuditService;
import com.calderwood.meridian.platform.Anomalies;
import com.calderwood.meridian.security.Actor;
import com.calderwood.meridian.security.CurrentActor;
import jakarta.servlet.http.HttpServletRequest;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;
import org.w3c.dom.Document;
import org.w3c.dom.Node;
import org.w3c.dom.NodeList;

/** Consignment intake: documents pasted in, and manifests uploaded from a terminal system. */
@RestController
@RequestMapping("/api/intake")
public class IntakeController {

    private final DocumentParser parser;
    private final JdbcTemplate jdbc;
    private final AuditService audit;

    public IntakeController(DocumentParser parser, JdbcTemplate jdbc, AuditService audit) {
        this.parser = parser;
        this.jdbc = jdbc;
        this.audit = audit;
    }

    /**
     * Accept one consignment document.
     *
     * <p>The forwarders' own systems post this directly; the screen has a paste box for
     * the desk to replay one by hand when a submission has been rejected.
     */
    @PostMapping(value = "/documents", consumes = {MediaType.APPLICATION_XML_VALUE, MediaType.TEXT_XML_VALUE})
    public ResponseEntity<Map<String, Object>> document(@RequestBody byte[] body,
                                                        HttpServletRequest request) {
        Actor caller = CurrentActor.required();
        Document parsed;
        try {
            parsed = parser.parse(body, Anomalies.INTAKE_ENTITY_RESOLVED, "body", false).document();
        } catch (IOException | org.xml.sax.SAXException malformed) {
            return ResponseEntity.badRequest().body(Map.of(
                    "accepted", false,
                    "error", "The document could not be read as XML."));
        }

        String reference = text(parsed, "reference");
        Map<String, Object> body0 = new LinkedHashMap<>();
        body0.put("accepted", true);
        body0.put("reference", reference);
        body0.put("elements", count(parsed));
        audit.record(caller.id(), "intake.document", "consignment", reference,
                caller.accountId(), request, "submitted through the console");
        return ResponseEntity.ok(body0);
    }

    /**
     * Accept a manifest file.
     *
     * <p>Terminal operating systems write a file rather than posting a body, so this is
     * an upload. Same reader as the document endpoint.
     */
    @PostMapping(value = "/manifests", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public ResponseEntity<Map<String, Object>> manifest(@RequestParam("manifest") MultipartFile manifest,
                                                        HttpServletRequest request) {
        Actor caller = CurrentActor.required();
        if (manifest == null || manifest.isEmpty()) {
            return ResponseEntity.badRequest().body(Map.of("accepted", false,
                    "error", "No manifest was attached."));
        }
        int lines;
        try {
            DocumentParser.Parsed parsed = parser.parse(manifest.getBytes(),
                    Anomalies.MANIFEST_ENTITY_RESOLVED_REMOTE, "manifest", true);
            lines = count(parsed.document());
        } catch (IOException | org.xml.sax.SAXException malformed) {
            return ResponseEntity.badRequest().body(Map.of("accepted", false,
                    "error", "The manifest could not be read as XML."));
        }
        audit.record(caller.id(), "intake.manifest", "manifest", manifest.getOriginalFilename(),
                caller.accountId(), request, lines + " lines");
        // Terse on purpose: the terminal systems poll this and only read the count.
        return ResponseEntity.ok(Map.of("accepted", true, "lines", lines));
    }

    /** What has come in recently. */
    @GetMapping("/history")
    public Map<String, Object> history(@RequestParam(defaultValue = "0") int page,
                                       @RequestParam(defaultValue = "25") int size) {
        Actor caller = CurrentActor.required();
        int limit = Math.min(Math.max(size, 1), 100);
        List<Map<String, Object>> rows = jdbc.queryForList(
                "SELECT id, occurred_at, action, object_reference, detail FROM audit_events"
                        + " WHERE action LIKE 'intake.%' AND (? IS NULL OR account_id = ?)"
                        + " ORDER BY occurred_at DESC LIMIT ? OFFSET ?",
                caller.isAdministrator() ? null : caller.accountId(),
                caller.isAdministrator() ? null : caller.accountId(),
                limit, Math.max(page, 0) * limit);
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("submissions", rows);
        body.put("page", Math.max(page, 0));
        body.put("size", limit);
        return body;
    }

    private static String text(Document document, String element) {
        NodeList nodes = document.getElementsByTagName(element);
        if (nodes.getLength() == 0) {
            return null;
        }
        String value = nodes.item(0).getTextContent();
        return value == null ? null : value.trim();
    }

    private static int count(Document document) {
        int total = 0;
        NodeList all = document.getElementsByTagName("*");
        for (int i = 0; i < all.getLength(); i++) {
            if (all.item(i).getNodeType() == Node.ELEMENT_NODE) {
                total++;
            }
        }
        return total;
    }

    /** Charset spelled out so a forwarder's encoding declaration is what decides. */
    static String decode(byte[] body) {
        return new String(body, StandardCharsets.UTF_8);
    }
}
