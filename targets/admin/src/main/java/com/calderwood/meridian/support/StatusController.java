package com.calderwood.meridian.support;

import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** What the console tells an operator when something looks wrong. */
@RestController
@RequestMapping("/api")
public class StatusController {

    private final String release;
    private final Instant startedAt = Instant.now();

    public StatusController(@Value("${meridian.release:4.11.2}") String release) {
        this.release = release;
    }

    /** The status strip at the foot of every screen. */
    @GetMapping("/status")
    public Map<String, Object> status() {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("service", "meridian");
        body.put("release", release);
        body.put("state", "operational");
        body.put("since", startedAt.toString());
        body.put("support", "servicedesk@calderwood.example");
        return body;
    }
}
