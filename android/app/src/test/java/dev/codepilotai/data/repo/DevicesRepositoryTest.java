package dev.codepilotai.data.repo;

import static com.google.common.truth.Truth.assertThat;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import android.content.Context;
import android.content.SharedPreferences;
import com.squareup.moshi.Moshi;
import dev.codepilotai.data.api.ApiException;
import dev.codepilotai.data.api.DevicesApi;
import io.reactivex.rxjava3.observers.TestObserver;
import java.io.IOException;
import java.util.concurrent.TimeUnit;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import retrofit2.Retrofit;
import retrofit2.adapter.rxjava3.RxJava3CallAdapterFactory;
import retrofit2.converter.moshi.MoshiConverterFactory;

/** Device registration, token-change deduping, and sign-out teardown. */
public final class DevicesRepositoryTest {

    private static final long AWAIT_SECONDS = 5;

    private MockWebServer server;
    private DevicesApi api;

    @Before
    public void setUp() throws IOException {
        server = new MockWebServer();
        server.start();
        api = new Retrofit.Builder()
                .baseUrl(server.url("/"))
                .addConverterFactory(MoshiConverterFactory.create(new Moshi.Builder().build()))
                .addCallAdapterFactory(RxJava3CallAdapterFactory.create())
                .build()
                .create(DevicesApi.class);
    }

    @After
    public void tearDown() throws IOException {
        server.shutdown();
    }

    private static DevicesRepository repositoryWithToken(DevicesApi api, String storedToken) {
        Context context = mock(Context.class);
        SharedPreferences prefs = mock(SharedPreferences.class);
        SharedPreferences.Editor editor = mock(SharedPreferences.Editor.class);
        when(context.getSharedPreferences(anyString(), anyInt())).thenReturn(prefs);
        when(prefs.getString("registered_token", null)).thenReturn(storedToken);
        when(prefs.edit()).thenReturn(editor);
        when(editor.putString(anyString(), anyString())).thenReturn(editor);
        when(editor.remove(anyString())).thenReturn(editor);
        return new DevicesRepository(api, context);
    }

    private static void await(TestObserver<?> observer) throws InterruptedException {
        observer.awaitDone(AWAIT_SECONDS, TimeUnit.SECONDS);
    }

    @Test
    public void registerIfChanged_sameToken_skipsTheNetwork() {
        DevicesRepository repository = repositoryWithToken(api, "tok-1");

        repository.registerIfChanged("tok-1").test().assertComplete();
        assertThat(server.getRequestCount()).isEqualTo(0);
    }

    @Test
    public void registerIfChanged_newToken_postsAndRemembers() throws InterruptedException {
        server.enqueue(json(201, "{\"id\":1,\"token\":\"tok-2\",\"platform\":\"android\"}"));
        DevicesRepository repository = repositoryWithToken(api, null);

        TestObserver<Void> observer = repository.registerIfChanged("tok-2").test();
        await(observer);
        observer.assertComplete();

        RecordedRequest request = server.takeRequest();
        assertThat(request.getPath()).isEqualTo("/devices");
        assertThat(request.getMethod()).isEqualTo("POST");
        String body = request.getBody().readUtf8();
        assertThat(body).contains("\"token\":\"tok-2\"");
        assertThat(body).contains("\"platform\":\"android\"");
    }

    @Test
    public void register_httpError_surfacesApiException() throws InterruptedException {
        server.enqueue(json(400, "{\"error\":{\"code\":\"invalid\",\"message\":\"nope\"}}"));
        DevicesRepository repository = repositoryWithToken(api, null);

        TestObserver<Void> observer = repository.register("tok", "android").test();
        await(observer);
        observer.assertError(ApiException.class);
    }

    @Test
    public void register_blankToken_doesNothing() {
        DevicesRepository repository = repositoryWithToken(api, null);

        repository.register("   ", "android").test().assertComplete();
        assertThat(server.getRequestCount()).isEqualTo(0);
    }

    @Test
    public void unregisterCurrent_postsTheRememberedToken() throws InterruptedException {
        server.enqueue(new MockResponse().setResponseCode(204));
        DevicesRepository repository = repositoryWithToken(api, "tok-9");

        TestObserver<Void> observer = repository.unregisterCurrent().test();
        await(observer);
        observer.assertComplete();

        RecordedRequest request = server.takeRequest();
        assertThat(request.getPath()).isEqualTo("/devices/unregister");
        assertThat(request.getBody().readUtf8()).contains("\"token\":\"tok-9\"");
    }

    @Test
    public void unregisterCurrent_noToken_doesNothing() {
        DevicesRepository repository = repositoryWithToken(api, null);

        repository.unregisterCurrent().test().assertComplete();
        assertThat(server.getRequestCount()).isEqualTo(0);
    }

    @Test
    public void unregisterCurrent_failure_stillCompletesLocally() throws InterruptedException {
        server.enqueue(json(500, "{\"error\":{\"code\":\"boom\",\"message\":\"boom\"}}"));
        Context context = mock(Context.class);
        SharedPreferences prefs = mock(SharedPreferences.class);
        SharedPreferences.Editor editor = mock(SharedPreferences.Editor.class);
        when(context.getSharedPreferences(anyString(), anyInt())).thenReturn(prefs);
        when(prefs.getString("registered_token", null)).thenReturn("tok-9");
        when(prefs.edit()).thenReturn(editor);
        when(editor.remove(anyString())).thenReturn(editor);
        DevicesRepository repository = new DevicesRepository(api, context);

        TestObserver<Void> observer = repository.unregisterCurrent().test();
        await(observer);

        observer.assertComplete();
        verify(editor).remove("registered_token");
    }

    @Test
    public void unregisterCurrent_blankStoredToken_doesNotEditPrefs() {
        Context context = mock(Context.class);
        SharedPreferences prefs = mock(SharedPreferences.class);
        when(context.getSharedPreferences(anyString(), anyInt())).thenReturn(prefs);
        when(prefs.getString("registered_token", null)).thenReturn("  ");
        DevicesRepository repository = new DevicesRepository(api, context);

        repository.unregisterCurrent().test().assertComplete();
        verify(prefs, never()).edit();
        assertThat(server.getRequestCount()).isEqualTo(0);
    }

    private static MockResponse json(int code, String body) {
        return new MockResponse()
                .setResponseCode(code)
                .addHeader("Content-Type", "application/json")
                .setBody(body);
    }
}
