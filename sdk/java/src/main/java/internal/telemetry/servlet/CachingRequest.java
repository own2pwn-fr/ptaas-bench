package internal.telemetry.servlet;

import jakarta.servlet.ReadListener;
import jakarta.servlet.ServletInputStream;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletRequestWrapper;
import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.Charset;
import java.nio.charset.StandardCharsets;

/**
 * A request whose body is remembered as the application reads it.
 *
 * <p>The obvious implementation — read the body in the filter, then hand the
 * application a fresh stream over the bytes — is wrong on the Servlet API, and wrong in
 * a way that only shows up in production. Form parameters are parsed lazily by the
 * container from the <em>container's own</em> request object, not through a wrapper, so
 * a filter that drains the stream first leaves {@code getParameter} answering
 * {@code null} for every field the client sent. Handlers then behave as though the form
 * were empty.
 *
 * <p>So nothing is read ahead of the application. Reads are teed into a bounded buffer
 * as they happen, which changes no behaviour at all: the same bytes arrive, in the same
 * order, at the same time, and the container's own parameter parsing is untouched. What
 * the application chose not to read, the filter collects afterwards — see
 * {@link #drainRemaining()} — once the response has already been produced, so that work
 * cannot land in the endpoint's latency.
 *
 * <p>The buffer is capped. Past the cap the bytes still flow to the application and
 * only the prefix is remembered, because an upload of a few hundred megabytes must not
 * become a few hundred megabytes of heap in an observability agent.
 */
public final class CachingRequest extends HttpServletRequestWrapper {

    private final int limit;
    private final ByteArrayOutputStream buffer = new ByteArrayOutputStream(512);
    private CachingStream stream;
    private BufferedReader reader;
    private boolean overflowed;

    public CachingRequest(HttpServletRequest request, int limit) {
        super(request);
        this.limit = Math.max(0, limit);
    }

    @Override
    public ServletInputStream getInputStream() throws IOException {
        if (reader != null) {
            // The Servlet API forbids using both; whichever the application asked for
            // first is the one it gets, and raising here would be a behaviour change.
            return super.getInputStream();
        }
        if (stream == null) {
            stream = new CachingStream(super.getInputStream());
        }
        return stream;
    }

    @Override
    public BufferedReader getReader() throws IOException {
        if (stream != null) {
            return super.getReader();
        }
        if (reader == null) {
            reader = new BufferedReader(new InputStreamReader(getInputStreamForReader(), charset()));
        }
        return reader;
    }

    private ServletInputStream getInputStreamForReader() throws IOException {
        if (stream == null) {
            stream = new CachingStream(super.getInputStream());
        }
        return stream;
    }

    private Charset charset() {
        String encoding = getCharacterEncoding();
        if (encoding != null) {
            try {
                return Charset.forName(encoding);
            } catch (IllegalArgumentException unknown) {
                return StandardCharsets.UTF_8;
            }
        }
        return StandardCharsets.UTF_8;
    }

    /** The bytes seen so far. Never longer than the configured cap. */
    public byte[] captured() {
        return buffer.toByteArray();
    }

    public boolean isOverflowed() {
        return overflowed;
    }

    /**
     * Read whatever the application left behind.
     *
     * <p>Called from the filter after the chain has returned, i.e. after the response
     * body has been produced. A handler that ignores its body — a 404, a route that only
     * looks at the path, a rejected request — would otherwise leave nothing to describe,
     * and "the client sent this payload and the endpoint refused it" is exactly the
     * record worth having.
     *
     * <p>Best effort by construction: on a connection the container has already begun
     * to reset there may be nothing left to read, and that is not an error.
     */
    public void drainRemaining() {
        if (overflowed || buffer.size() >= limit) {
            return;
        }
        try {
            ServletInputStream in = stream != null ? stream : getInputStream();
            byte[] chunk = new byte[4096];
            while (buffer.size() < limit) {
                int read = in.read(chunk);
                if (read <= 0) {
                    return;
                }
                record(chunk, 0, read);
            }
        } catch (IOException | RuntimeException gone) {
            // The stream is finished, reset, or the container has taken it back.
        }
    }

    private void record(byte[] data, int offset, int length) {
        int room = limit - buffer.size();
        if (room <= 0) {
            overflowed = true;
            return;
        }
        int take = Math.min(room, length);
        buffer.write(data, offset, take);
        if (take < length) {
            overflowed = true;
        }
    }

    /**
     * A pass-through stream that copies what it hands over.
     *
     * <p>Every method delegates, including the asynchronous ones: a service reading its
     * body with a {@link ReadListener} must keep working, and a wrapper that quietly
     * dropped that support would break exactly the endpoints that stream large uploads.
     */
    private final class CachingStream extends ServletInputStream {

        private final ServletInputStream delegate;

        CachingStream(ServletInputStream delegate) {
            this.delegate = delegate;
        }

        @Override
        public int read() throws IOException {
            int value = delegate.read();
            if (value >= 0) {
                record(new byte[]{(byte) value}, 0, 1);
            }
            return value;
        }

        @Override
        public int read(byte[] b, int off, int len) throws IOException {
            int read = delegate.read(b, off, len);
            if (read > 0) {
                record(b, off, read);
            }
            return read;
        }

        @Override
        public boolean isFinished() {
            return delegate.isFinished();
        }

        @Override
        public boolean isReady() {
            return delegate.isReady();
        }

        @Override
        public void setReadListener(ReadListener listener) {
            delegate.setReadListener(listener);
        }

        @Override
        public int available() throws IOException {
            return delegate.available();
        }

        @Override
        public void close() throws IOException {
            delegate.close();
        }
    }
}
