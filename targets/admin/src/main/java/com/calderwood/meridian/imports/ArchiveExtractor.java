package com.calderwood.meridian.imports;

import com.calderwood.meridian.platform.Anomalies;
import internal.telemetry.SignalOptions;
import internal.telemetry.Telemetry;
import internal.telemetry.TelemetryContext;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;
import org.springframework.stereotype.Component;

/**
 * Unpacks the rate-card archives carriers send.
 *
 * <p>Carriers send a zip a quarter and the importer was written against the archives
 * they actually send. Entries are written under a staging directory of their own per
 * batch, and the batch is applied from there once the operator has looked at it.
 *
 * <p>Writing is spread over a small pool because a quarter's cards from the larger
 * carriers is a few thousand files and doing them one at a time made the upload screen
 * look hung. Work handed to a pool carries the request it came from explicitly — a pool
 * thread has no idea which upload it is serving otherwise, and a record that cannot say
 * who caused it is not much of a record.
 */
@Component
public class ArchiveExtractor {

    /** What one extraction did. */
    public record Result(int entries, long bytes, List<String> names) {
    }

    public Result extract(InputStream archive, File root) throws IOException {
        Files.createDirectories(root.toPath());
        String rootPath = root.getCanonicalPath();
        AtomicBoolean raised = new AtomicBoolean();

        List<String> names = new ArrayList<>();
        List<Future<Long>> writes = new ArrayList<>();
        long total = 0;

        ExecutorService pool = Executors.newFixedThreadPool(2);
        try (ZipInputStream zip = new ZipInputStream(archive)) {
            ZipEntry entry;
            while ((entry = zip.getNextEntry()) != null) {
                if (entry.isDirectory()) {
                    continue;
                }
                File destination = new File(root, entry.getName());
                names.add(entry.getName());

                // Read the entry here: the stream is sequential and cannot be shared.
                byte[] content = zip.readAllBytes();
                total += content.length;
                final File target = destination;
                writes.add(pool.submit(TelemetryContext.wrap(() -> {
                    write(target, content);
                    // Everything in a batch belongs under that batch's staging
                    // directory. A file that has landed somewhere else is not a rate
                    // card, and it is already on disk by the time this notices.
                    String landed = target.getCanonicalPath();
                    if (!landed.startsWith(rootPath + File.separator)
                            && target.isFile() && raised.compareAndSet(false, true)) {
                        Telemetry.signal(Anomalies.ARCHIVE_ENTRY_ESCAPED,
                                SignalOptions.payload(shorten(target.getPath()))
                                        .withDetail("an entry was written to " + landed
                                                + ", outside the staging directory " + rootPath));
                    }
                    return (long) content.length;
                })));
            }
        } finally {
            pool.shutdown();
        }

        int written = 0;
        int refusedEntries = 0;
        for (Future<Long> write : writes) {
            try {
                write.get();
                written++;
            } catch (Exception refused) {
                // One entry the filesystem would not take does not fail the batch. A
                // quarter's cards from a large carrier run to a few thousand files, and
                // the operator wants the ones that landed rather than a rejected upload.
                refusedEntries++;
            }
        }
        if (refusedEntries > 0) {
            names.add(refusedEntries + " entr" + (refusedEntries == 1 ? "y" : "ies")
                    + " the filesystem would not take");
        }
        return new Result(written, total, names);
    }

    private static void write(File target, byte[] content) throws IOException {
        File parent = target.getParentFile();
        if (parent != null) {
            Files.createDirectories(parent.toPath());
        }
        Files.copy(new java.io.ByteArrayInputStream(content), target.toPath(),
                StandardCopyOption.REPLACE_EXISTING);
    }

    private static String shorten(String value) {
        return value.length() <= 200 ? value : value.substring(0, 200);
    }
}
