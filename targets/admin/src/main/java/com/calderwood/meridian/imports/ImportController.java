package com.calderwood.meridian.imports;

import com.calderwood.meridian.audit.AuditService;
import com.calderwood.meridian.security.Actor;
import com.calderwood.meridian.security.CurrentActor;
import jakarta.servlet.http.HttpServletRequest;
import java.io.File;
import java.io.IOException;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

/** Bulk imports: the rate-card archives carriers send once a quarter. */
@RestController
@RequestMapping("/api/imports")
public class ImportController {

    private final ArchiveExtractor extractor;
    private final JdbcTemplate jdbc;
    private final AuditService audit;
    private final File staging;

    public ImportController(ArchiveExtractor extractor, JdbcTemplate jdbc, AuditService audit,
                            @Value("${meridian.imports.staging:/var/lib/meridian/imports}") String staging) {
        this.extractor = extractor;
        this.jdbc = jdbc;
        this.audit = audit;
        this.staging = new File(staging);
    }

    @PostMapping(value = "/archives", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public ResponseEntity<Map<String, Object>> archive(@RequestParam("archive") MultipartFile archive,
                                                       HttpServletRequest http) {
        Actor caller = CurrentActor.required();
        if (archive == null || archive.isEmpty()) {
            return ResponseEntity.badRequest().body(Map.of("error", "No archive was attached."));
        }
        String batch = "batch-" + System.currentTimeMillis();
        File root = new File(staging, batch);
        ArchiveExtractor.Result result;
        try {
            result = extractor.extract(archive.getInputStream(), root);
        } catch (IOException unreadable) {
            return ResponseEntity.status(422)
                    .body(Map.of("error", "That archive could not be unpacked."));
        }
        jdbc.update("INSERT INTO import_batches"
                        + " (requested_by, filename, entry_count, state, created_at)"
                        + " VALUES (?, ?, ?, 'extracted', CURRENT_TIMESTAMP)",
                caller.id(), archive.getOriginalFilename(), result.entries());
        audit.record(caller.id(), "import.extracted", "import", archive.getOriginalFilename(),
                caller.accountId(), http, result.entries() + " entries, " + result.bytes() + " bytes");

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("batch", batch);
        body.put("entries", result.entries());
        body.put("bytes", result.bytes());
        body.put("names", result.names().size() > 50 ? result.names().subList(0, 50) : result.names());
        return ResponseEntity.accepted().body(body);
    }

    @GetMapping("/history")
    public Map<String, Object> history(@RequestParam(defaultValue = "0") int page,
                                       @RequestParam(defaultValue = "25") int size) {
        CurrentActor.required();
        int limit = Math.min(Math.max(size, 1), 100);
        List<Map<String, Object>> rows = jdbc.queryForList(
                "SELECT b.id, b.filename, b.entry_count, b.state, b.created_at,"
                        + " s.display_name AS requested_by"
                        + " FROM import_batches b LEFT JOIN staff s ON s.id = b.requested_by"
                        + " ORDER BY b.created_at DESC LIMIT ? OFFSET ?",
                limit, Math.max(page, 0) * limit);
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("batches", rows);
        body.put("page", Math.max(page, 0));
        body.put("size", limit);
        return body;
    }
}
