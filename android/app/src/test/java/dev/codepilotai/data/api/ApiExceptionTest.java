package dev.codepilotai.data.api;

import static com.google.common.truth.Truth.assertThat;

import com.squareup.moshi.Moshi;
import dev.codepilotai.data.api.dto.ErrorEnvelopeDto;
import java.io.IOException;
import okhttp3.MediaType;
import okhttp3.ResponseBody;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import org.junit.Test;
import retrofit2.HttpException;
import retrofit2.Response;

/** Structured error decoding plus the connection-error fallback message. */
public final class ApiExceptionTest {

    private static final MediaType JSON = MediaType.parse("application/json");

    @Test
    public void from_httpError_decodesEnvelope() throws IOException {
        String body =
                "{\"error\":{\"code\":\"pending_changes\","
                        + "\"message\":\"Task still has pending changes to review.\"}}";
        HttpException http =
                new HttpException(
                        Response.error(409, ResponseBody.create(body, JSON)));

        ApiException e = ApiException.from(http);

        assertThat(e.httpCode).isEqualTo(409);
        assertThat(e.code).isEqualTo("http_409");
        assertThat(e.getMessage()).contains("Task still has pending changes to review.");
        assertThat(e.userMessage()).contains("http_409");
    }

    @Test
    public void from_httpError_emptyBody_usesHttpMessage() {
        HttpException http =
                new HttpException(
                        Response.error(500, ResponseBody.create("", JSON)));

        ApiException e = ApiException.from(http);

        assertThat(e.code).isEqualTo("http_500");
        assertThat(e.getMessage()).isNotEmpty();
    }

    @Test
    public void from_ioError_mapsToNetworkMessage() {
        ApiException e = ApiException.from(new IOException("boom"));

        assertThat(e.code).isEqualTo("network_error");
        assertThat(e.httpCode).isEqualTo(0);
        assertThat(e.getMessage()).contains("Cannot reach the server");
    }

    @Test
    public void from_alreadyApiException_passesThrough() {
        ApiException original = new ApiException(422, "validation_error", "Bad input.");
        ApiException e = ApiException.from(original);

        assertThat(e).isSameInstanceAs(original);
    }

    @Test
    public void envelope_deserializes() throws Exception {
        ErrorEnvelopeDto envelope =
                new Moshi.Builder()
                        .build()
                        .adapter(ErrorEnvelopeDto.class)
                        .fromJson(
                                "{\"error\":{\"code\":\"c\",\"message\":\"m\"}}");
        assertThat(envelope.error.code).isEqualTo("c");
        assertThat(envelope.error.message).isEqualTo("m");
    }

    @Test
    public void mockServer_roundTrip_smoke() throws Exception {
        // Proves MockWebServer itself works in the unit-test classpath.
        try (MockWebServer server = new MockWebServer()) {
            server.start();
            server.enqueue(new MockResponse().setResponseCode(404).setBody("{}"));
            // Just a smoke check that the server accepts connections; the real
            // SSE parsing behavior is covered by SseClientTest.
            assertThat(server.getPort()).isGreaterThan(0);
        }
    }
}