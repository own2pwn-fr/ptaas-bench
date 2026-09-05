package internal.telemetry.spring;

import internal.telemetry.TelemetryContext;
import org.springframework.core.task.TaskDecorator;

/**
 * Carries the in-flight request's facts across an asynchronous boundary.
 *
 * <p>An asynchronous method runs on a managed executor, on a thread that has no
 * relationship to the request that asked for the work. Without this, a counter raised
 * inside that work reports no peer, no request id and no classification — which is not
 * merely a gap: traffic the platform generated for itself would be counted as organic,
 * because the marker that says otherwise did not travel.
 *
 * <p>Capture happens when the task is submitted, on the submitting thread, which is the
 * only moment the caller's context is knowable.
 *
 * <p>{@link TelemetryConfiguration} installs it on every managed executor it can find,
 * so an ordinary service needs no change. Work handed to an executor the framework does
 * not manage is wrapped explicitly with {@code Telemetry.wrap(...)}.
 */
public final class TelemetryTaskDecorator implements TaskDecorator {

    @Override
    public Runnable decorate(Runnable runnable) {
        return TelemetryContext.wrap(runnable);
    }
}
