package com.calderwood.meridian.directory;

import com.calderwood.meridian.security.CurrentActor;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** The staff directory screen. */
@RestController
@RequestMapping("/api/directory")
public class DirectoryController {

    private final DirectoryClient directory;

    public DirectoryController(DirectoryClient directory) {
        this.directory = directory;
    }

    /** Find people by surname or display name. */
    @GetMapping("/people")
    public Map<String, Object> people(@RequestParam(defaultValue = "") String surname,
                                      @RequestParam(required = false) String department,
                                      @RequestParam(defaultValue = "0") int page,
                                      @RequestParam(defaultValue = "25") int size) {
        CurrentActor.required();
        int limit = Math.min(Math.max(size, 1), 100);
        List<Map<String, List<String>>> entries = directory.people(surname, limit + 1);

        List<Map<String, Object>> out = new ArrayList<>();
        for (Map<String, List<String>> entry : entries) {
            if (department != null && !department.isBlank()
                    && !department.equalsIgnoreCase(DirectoryClient.first(entry, "departmentNumber"))) {
                continue;
            }
            Map<String, Object> person = new LinkedHashMap<>();
            person.put("uid", DirectoryClient.first(entry, "uid"));
            person.put("displayName", DirectoryClient.first(entry, "cn"));
            person.put("familyName", DirectoryClient.first(entry, "sn"));
            person.put("givenName", DirectoryClient.first(entry, "givenName"));
            person.put("mail", DirectoryClient.first(entry, "mail"));
            person.put("telephone", DirectoryClient.first(entry, "telephoneNumber"));
            person.put("title", DirectoryClient.first(entry, "title"));
            person.put("department", DirectoryClient.first(entry, "departmentNumber"));
            person.put("team", DirectoryClient.first(entry, "ou"));
            // Desk and shared accounts carry their operating notes here instead of a
            // title, and the screen shows them in the same column.
            person.put("notes", entry.get("description"));
            person.put("host", DirectoryClient.first(entry, "serialNumber"));
            out.add(person);
        }

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("people", out);
        body.put("surname", surname);
        body.put("page", Math.max(page, 0));
        body.put("size", limit);
        return body;
    }

    @GetMapping("/people/{uid}")
    public ResponseEntity<Map<String, Object>> person(@PathVariable String uid) {
        CurrentActor.required();
        return directory.person(uid)
                .<ResponseEntity<Map<String, Object>>>map(entry -> {
                    Map<String, Object> person = new LinkedHashMap<>();
                    person.put("uid", DirectoryClient.first(entry, "uid"));
                    person.put("displayName", DirectoryClient.first(entry, "cn"));
                    person.put("familyName", DirectoryClient.first(entry, "sn"));
                    person.put("givenName", DirectoryClient.first(entry, "givenName"));
                    person.put("mail", DirectoryClient.first(entry, "mail"));
                    person.put("telephone", DirectoryClient.first(entry, "telephoneNumber"));
                    person.put("title", DirectoryClient.first(entry, "title"));
                    person.put("department", DirectoryClient.first(entry, "departmentNumber"));
                    person.put("team", DirectoryClient.first(entry, "ou"));
                    return ResponseEntity.ok(person);
                })
                .orElseGet(() -> ResponseEntity.notFound().build());
    }
}
