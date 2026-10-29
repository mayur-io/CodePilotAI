package dev.codepilotai.data.api;

import dev.codepilotai.data.prefs.TokenStore;
import java.io.IOException;
import okhttp3.Interceptor;
import okhttp3.Request;
import okhttp3.Response;

/** Attaches the bearer access token and retries once after refreshing on 401. */
public final class AuthInterceptor implements Interceptor {

    private final TokenStore tokenStore;
    private final TokenRefresher refresher;

    public AuthInterceptor(TokenStore tokenStore, TokenRefresher refresher) {
        this.tokenStore = tokenStore;
        this.refresher = refresher;
    }

    @Override
    public Response intercept(Chain chain) throws IOException {
        Request original = chain.request();
        String access = tokenStore.accessToken();
        Request request = original;
        if (access != null && !access.isEmpty()) {
            request = original.newBuilder().header("Authorization", "Bearer " + access).build();
        }
        Response response = chain.proceed(request);
        if (response.code() == 401 && isAuthRoute(original)) {
            response.close();
            if (refresher.refreshIfNeeded(response)) {
                String newAccess = tokenStore.accessToken();
                if (newAccess != null && !newAccess.isEmpty()) {
                    Request retried = original.newBuilder()
                            .header("Authorization", "Bearer " + newAccess)
                            .header("X-Token-Refreshed", "1")
                            .build();
                    return chain.proceed(retried);
                }
            }
        }
        return response;
    }

    private static boolean isAuthRoute(Request request) {
        String path = request.url().encodedPath();
        return !(path.contains("/auth/refresh") || path.contains("/auth/exchange")
                || path.contains("/auth/github"));
    }
}