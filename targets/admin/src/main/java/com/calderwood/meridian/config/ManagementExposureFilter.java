package com.calderwood.meridian.config;

import com.calderwood.meridian.platform.Anomalies;
import com.calderwood.meridian.security.CurrentActor;
import internal.telemetry.SignalOptions;
import internal.telemetry.Telemetry;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.ServletOutputStream;
import jakarta.servlet.WriteListener;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import jakarta.servlet.http.HttpServletResponseWrapper;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Watches what the management endpoints actually hand out.
 *
 * <p>These endpoints were opened up during the memory investigation, behind a network
 * policy that was supposed to keep the management port to the estate. Whether that
 * policy is doing its job is not something the application can assert, so instead it
 * counts the times one of the revealing endpoints answered a caller who had not signed
 * in, and the times a served log body still carried a secret in clear.
 *
 * <p>Only these paths are buffered. Buffering every response would put the whole of the
 * console's output through an extra copy for no reason.
 */
@Component
@Order(Ordered.LOWEST_PRECEDENCE - 100)
public class ManagementExposureFilter extends OncePerRequestFilter {

    /** Endpoints that describe the deployment rather than its health. */
    private static final Set<String> REVEALING = Set.of(
            "env", "configprops", "beans", "mappings", "threaddump", "heapdump",
            "loggers", "conditions", "scheduledtasks", "caches", "metrics", "logfile");

    /** Cap on what is inspected, so a large log does not become a large buffer. */
    private static final int INSPECT_MAX_BYTES = 4 * 1024 * 1024;

    private final JdbcTemplate jdbc;

    public ManagementExposureFilter(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        String path = request.getRequestURI();
        return path == null || !path.startsWith("/actuator/");
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
                                    FilterChain chain) throws ServletException, IOException {
        String endpoint = endpointOf(request.getRequestURI());
        if (!REVEALING.contains(endpoint)) {
            chain.doFilter(request, response);
            return;
        }

        BufferingResponse buffered = new BufferingResponse(response);
        try {
            chain.doFilter(request, buffered);
        } finally {
            buffered.settle();
            inspect(endpoint, request, buffered.getStatus(), buffered.copy());
        }
    }

    private void inspect(String endpoint, HttpServletRequest request, int status, byte[] body) {
        if (status != 200 || body.length == 0) {
            return;
        }
        if (CurrentActor.get() == null) {
            Telemetry.signal(Anomalies.MANAGEMENT_INTERNALS_SERVED,
                    SignalOptions.payload(request.getRequestURI())
                            .withDetail(body.length + " bytes of " + endpoint
                                    + " served to a caller with no session"));
        }
        if ("logfile".equals(endpoint)) {
            String text = new String(body, StandardCharsets.UTF_8);
            for (String secret : liveSecrets()) {
                if (secret != null && secret.length() >= 12 && text.contains(secret)) {
                    Telemetry.signal(Anomalies.LOGFILE_CREDENTIAL_SERVED,
                            SignalOptions.payload(request.getRequestURI())
                                    .withDetail("a served log body carried an interface secret in"
                                            + " clear, matching one currently stored"));
                    return;
                }
            }
        }
    }

    private List<String> liveSecrets() {
        try {
            return jdbc.queryForList(
                    "SELECT secret FROM integrations WHERE secret <> ''", String.class);
        } catch (RuntimeException unavailable) {
            return List.of();
        }
    }

    private static String endpointOf(String path) {
        String tail = path.substring("/actuator/".length());
        int slash = tail.indexOf('/');
        return (slash < 0 ? tail : tail.substring(0, slash)).toLowerCase(Locale.ROOT);
    }

    /** Passes the body straight through, keeping a copy of the first few megabytes. */
    private static final class BufferingResponse extends HttpServletResponseWrapper {

        private final ByteArrayOutputStream buffer = new ByteArrayOutputStream(8192);
        private ServletOutputStream stream;
        private PrintWriter writer;

        BufferingResponse(HttpServletResponse response) {
            super(response);
        }

        @Override
        public ServletOutputStream getOutputStream() throws IOException {
            if (stream == null) {
                ServletOutputStream delegate = getResponse().getOutputStream();
                stream = new ServletOutputStream() {
                    @Override
                    public void write(int b) throws IOException {
                        delegate.write(b);
                        record(new byte[]{(byte) b}, 0, 1);
                    }

                    @Override
                    public void write(byte[] b, int off, int len) throws IOException {
                        delegate.write(b, off, len);
                        record(b, off, len);
                    }

                    @Override
                    public void flush() throws IOException {
                        delegate.flush();
                    }

                    @Override
                    public boolean isReady() {
                        return delegate.isReady();
                    }

                    @Override
                    public void setWriteListener(WriteListener listener) {
                        delegate.setWriteListener(listener);
                    }
                };
            }
            return stream;
        }

        @Override
        public PrintWriter getWriter() throws IOException {
            if (writer == null) {
                writer = new PrintWriter(new java.io.OutputStreamWriter(
                        getOutputStream(), StandardCharsets.UTF_8), true);
            }
            return writer;
        }

        /**
         * Copy what went out, up to the cap.
         *
         * <p>The client always gets every byte; only the copy stops, because a log that
         * has grown to a gigabyte must not become a gigabyte of heap on the way past.
         */
        private void record(byte[] data, int off, int len) {
            int room = INSPECT_MAX_BYTES - buffer.size();
            if (room <= 0) {
                return;
            }
            buffer.write(data, off, Math.min(room, len));
        }

        byte[] copy() {
            return buffer.toByteArray();
        }

        void settle() throws IOException {
            if (writer != null) {
                writer.flush();
            }
        }
    }
}
