package internal.telemetry;

import java.util.List;
import java.util.Map;

/**
 * Where a batch goes instead of onto a socket.
 *
 * <p>Exists so that a service's own tests can assert on what it records without
 * standing up a collector, and so that a host with an unusual egress policy can supply
 * its own writer. Implementations run on the exporter thread and must not raise: an
 * implementation that does is counted as a send failure and nothing else.
 */
@FunctionalInterface
public interface BatchSender {

    void send(String path, List<Map<String, Object>> batch);
}
