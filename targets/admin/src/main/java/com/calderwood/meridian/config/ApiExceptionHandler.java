package com.calderwood.meridian.config;

import com.calderwood.meridian.security.CurrentActor;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/**
 * One error shape for the whole API.
 *
 * <p>The console shows the message, so it has to be something an operator can act on.
 * Nothing internal goes in it: a stack trace on a screen is a support call about the
 * stack trace rather than about the thing that went wrong.
 */
@RestControllerAdvice
public class ApiExceptionHandler {

    @ExceptionHandler(CurrentActor.NotSignedIn.class)
    public ResponseEntity<Map<String, Object>> notSignedIn() {
        return body(HttpStatus.UNAUTHORIZED, "Your session has ended. Sign in again to continue.");
    }

    @ExceptionHandler(IllegalArgumentException.class)
    public ResponseEntity<Map<String, Object>> badInput(IllegalArgumentException problem) {
        return body(HttpStatus.BAD_REQUEST,
                problem.getMessage() == null ? "That request could not be understood."
                        : problem.getMessage());
    }

    private static ResponseEntity<Map<String, Object>> body(HttpStatus status, String message) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("status", status.value());
        out.put("error", message);
        out.put("at", Instant.now().toString());
        return ResponseEntity.status(status).body(out);
    }
}
