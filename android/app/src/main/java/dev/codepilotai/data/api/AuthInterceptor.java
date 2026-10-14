package dev.codepilotai.data.api;

import dev.codepilotai.data.prefs.TokenStore;
import java.io.IOException;
import okhttp3.Interceptor;
import okhttp3.Request;
import okhttp3.Response;

/** Attaches the bearer access token to every authenticated request. */
public final class AuthInterceptor implements Interceptor {

    private final TokenStore tokenStore;

    public AuthInterceptor(TokenStore tokenStore) {
        this.tokenStore = tokenStore;
    }

    @Override
    public Response intercept(Chain chain) throws IOException {
        Request original = chain.request();
        String access = tokenStore.accessToken();
        if (access == null || access.isEmpty()) {
            return chain.proceed(original);
        }
        Request authenticated =
                original.newBuilder().header("Authorization", "Bearer " + access).build();
        return chain.proceed(authenticated);
    }
}