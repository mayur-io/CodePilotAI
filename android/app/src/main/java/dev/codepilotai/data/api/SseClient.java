package dev.codepilotai.data.api;

import com.squareup.moshi.Moshi;
import dev.codepilotai.data.api.dto.RunEventDto;
import dev.codepilotai.domain.model.RunEvent;
import io.reactivex.rxjava3.core.BackpressureStrategy;
import io.reactivex.rxjava3.core.Flowable;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.sse.EventSource;
import okhttp3.sse.EventSourceListener;
import okhttp3.sse.EventSources;

/**
 * Server-Sent Events client for {@code GET /tasks/{id}/stream/}.
 *
 * <p>The server's frames are {@code event: <kind>}, {@code data: {"seq":…}}
 * plus {@code close}/{@code timeout} control events and {@code : keep-alive}
 * comments. A completed or errored stream simply ends the {@link Flowable}; the
 * caller re-subscribes with the last {@code seq} to resume exactly where it
 * dropped (the event table is the durable buffer).
 */
public final class SseClient {

    private final OkHttpClient client;
    private final Moshi moshi;
    private final String baseUrl;

    public SseClient(OkHttpClient client, Moshi moshi, String baseUrl) {
        this.client = client;
        this.moshi = moshi;
        this.baseUrl = baseUrl.endsWith("/") ? baseUrl : baseUrl + "/";
    }

    public Flowable<RunEvent> stream(long taskId, long afterSeq) {
        return Flowable.create(
                emitter -> {
                    Request request =
                            new Request.Builder()
                                    .url(baseUrl + "tasks/" + taskId + "/stream/?after_seq=" + afterSeq)
                                    .header("Accept", "text/event-stream")
                                    .build();

                    EventSourceListener listener =
                            new EventSourceListener() {
                                @Override
                                public void onEvent(EventSource source, String id, String type, String data) {
                                    if ("close".equals(type) || "timeout".equals(type)) {
                                        emitter.onComplete();
                                        return;
                                    }
                                    RunEvent event = parse(data);
                                    if (event != null) {
                                        emitter.onNext(event);
                                    }
                                }

                                @Override
                                public void onClosed(EventSource source) {
                                    emitter.onComplete();
                                }

                                @Override
                                public void onFailure(EventSource source, Throwable t, Response response) {
                                    emitter.onError(t == null ? new RuntimeException("SSE closed") : t);
                                }
                            };

                    EventSource eventSource =
                            EventSources.createFactory(client).newEventSource(request, listener);
                    emitter.setCancellable(eventSource::cancel);
                },
                BackpressureStrategy.BUFFER);
    }

    private RunEvent parse(String data) {
        if (data == null || data.trim().isEmpty()) {
            return null;
        }
        try {
            RunEventDto dto = moshi.adapter(RunEventDto.class).fromJson(data);
            return dto == null ? null : dto.toDomain();
        } catch (Exception ignored) {
            return null;
        }
    }
}