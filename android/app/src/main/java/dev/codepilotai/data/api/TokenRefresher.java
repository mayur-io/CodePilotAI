package dev.codepilotai.data.api;

import dev.codepilotai.data.api.dto.TokenPairDto;
import dev.codepilotai.data.prefs.TokenStore;
import java.io.IOException;
import java.util.Collections;
import java.util.concurrent.atomic.AtomicBoolean;
import okhttp3.OkHttpClient;
import okhttp3.Response;
import okhttp3.logging.HttpLoggingInterceptor;
import retrofit2.Retrofit;
import retrofit2.converter.moshi.MoshiConverterFactory;

/**
 * Performs a single refresh and retries the original request once.
 */
public final class TokenRefresher {

    private final TokenStore tokenStore;
    private final String baseUrl;
    private final AtomicBoolean refreshing = new AtomicBoolean(false);
    private volatile AuthApi authApi;

    public TokenRefresher(TokenStore tokenStore, String baseUrl) {
        this.tokenStore = tokenStore;
        this.baseUrl = baseUrl;
    }

    private AuthApi authApi() {
        if (authApi == null) {
            synchronized (this) {
                if (authApi == null) {
                    HttpLoggingInterceptor logging = new HttpLoggingInterceptor();
                    logging.setLevel(HttpLoggingInterceptor.Level.BASIC);
                    OkHttpClient client = new OkHttpClient.Builder()
                            .addInterceptor(logging)
                            .build();
                    Retrofit retrofit = new Retrofit.Builder()
                            .baseUrl(baseUrl.endsWith("/") ? baseUrl : baseUrl + "/")
                            .client(client)
                            .addConverterFactory(MoshiConverterFactory.create(new com.squareup.moshi.Moshi.Builder().build()))
                            .build();
                    authApi = retrofit.create(AuthApi.class);
                }
            }
        }
        return authApi;
    }

    public boolean refreshIfNeeded(Response response) throws IOException {
        if (response.request().header("X-Token-Refreshed") != null) {
            return false;
        }
        String refresh = tokenStore.refreshToken();
        if (refresh == null || refresh.isEmpty()) {
            return false;
        }
        if (!refreshing.compareAndSet(false, true)) {
            return false;
        }
        try {
            java.util.HashMap<String, String> body = new java.util.HashMap<String, String>();
            body.put("refresh", refresh);
            dev.codepilotai.data.api.dto.TokenPairDto pair = authApi().refresh(body).blockingGet();
            if (pair == null || pair.access == null || pair.access.isEmpty()) {
                tokenStore.clear();
                return false;
            }
            tokenStore.save(pair.access, pair.refresh != null ? pair.refresh : refresh);
            return true;
        } catch (Exception e) {
            tokenStore.clear();
            return false;
        } finally {
            refreshing.set(false);
        }
    }
}
