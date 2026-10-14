package dev.codepilotai.data.api;

import static com.google.common.truth.Truth.assertThat;

import com.squareup.moshi.Moshi;
import dev.codepilotai.domain.model.RunEvent;
import io.reactivex.rxjava3.subscribers.TestSubscriber;
import java.util.concurrent.TimeUnit;
import okhttp3.OkHttpClient;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.Test;

/**
 * SSE frame parsing against a real MockWebServer. The default OkHttpClient read
 * timeout (10s) bounds every wait, so a stalled server cannot hang the tests.
 */
public final class SseClientTest {

    private static final String TS = "2025-01-01T00:00:00Z";

    private static String frame(String id, String type, String data) {
        return "id: " + id + "\n"
                + "event: " + type + "\n"
                + "data: " + data + "\n"
                + "\n";
    }

    private static SseClient client(String baseUrl) {
        return new SseClient(new OkHttpClient(), new Moshi.Builder().build(), baseUrl);
    }

    @Test
    public void stream_parsesFrames_andBuildsUrl() throws Exception {
        try (MockWebServer server = new MockWebServer()) {
            server.start();
            String body =
                    ": keep-alive\n"
                            + frame(
                                    "5",
                                    "log",
                                    "{\"seq\":5,\"kind\":\"log\",\"payload\":{\"message\":\"hello\"},"
                                            + "\"created_at\":\"" + TS + "\"}")
                            + frame(
                                    "6",
                                    "status",
                                    "{\"seq\":6,\"kind\":\"status\","
                                            + "\"payload\":{\"status\":\"running\"},\"created_at\":\"" + TS + "\"}");
            server.enqueue(
                    new MockResponse()
                            .setHeader("Content-Type", "text/event-stream")
                            .setBody(body));

            TestSubscriber<RunEvent> observer = new TestSubscriber<>();
            client(server.url("/").toString()).stream(42L, 3L).subscribe(observer);

            observer.awaitCount(2);
            observer.assertNoErrors();

            RunEvent first = observer.values().get(0);
            assertThat(first.seq).isEqualTo(5L);
            assertThat(first.kind).isEqualTo("log");
            assertThat(first.payload.get("message")).isEqualTo("hello");

            RunEvent second = observer.values().get(1);
            assertThat(second.seq).isEqualTo(6L);
            assertThat(second.kindEnum()).isEqualTo(RunEvent.Kind.STATUS);
            assertThat(second.payload.get("status")).isEqualTo("running");

            RecordedRequest request = server.takeRequest();
            assertThat(request.getPath()).isEqualTo("/tasks/42/stream/?after_seq=3");
            assertThat(request.getHeader("Accept")).isEqualTo("text/event-stream");
        }
    }

    @Test
    public void stream_closeControlEvent_completes() throws Exception {
        try (MockWebServer server = new MockWebServer()) {
            server.start();
            server.enqueue(
                    new MockResponse()
                            .setHeader("Content-Type", "text/event-stream")
                            .setBody("event: close\ndata: {}\n\n"));

            TestSubscriber<RunEvent> observer = new TestSubscriber<>();
            client(server.url("/").toString()).stream(1L, 0L).subscribe(observer);

            boolean completed = observer.await(10, TimeUnit.SECONDS);
            assertThat(completed).isTrue();
            observer.assertComplete();
        }
    }

    @Test
    public void stream_malformedData_isSkipped() throws Exception {
        try (MockWebServer server = new MockWebServer()) {
            server.start();
            String body = "event: log\ndata: {not-json}\n\n" + frame("7", "log",
                    "{\"seq\":7,\"kind\":\"log\",\"payload\":{\"message\":\"ok\"},\"created_at\":\"" + TS + "\"}");
            server.enqueue(
                    new MockResponse()
                            .setHeader("Content-Type", "text/event-stream")
                            .setBody(body));

            TestSubscriber<RunEvent> observer = new TestSubscriber<>();
            client(server.url("/").toString()).stream(1L, 0L).subscribe(observer);

            observer.awaitCount(1);
            observer.assertNoErrors();
            assertThat(observer.values().get(0).seq).isEqualTo(7L);
        }
    }
}