package dev.codepilotai.data.repo;

import android.content.Context;
import android.content.SharedPreferences;
import dev.codepilotai.data.api.ApiException;
import dev.codepilotai.data.api.DevicesApi;
import dev.codepilotai.data.api.dto.DeviceTokenRequest;
import io.reactivex.rxjava3.core.Completable;
import java.util.Collections;

/**
 * Registers this install's FCM token with the backend and remembers it so that
 * sign-out can unregister the device.
 *
 * <p>The remembered token lives in its own plain prefs file, deliberately kept
 * outside {@code TokenStore}: sign-out clears the JWT, but the server-side
 * device registration still has to be removed, which needs the token afterwards.
 */
public final class DevicesRepository {

    public static final String PLATFORM_ANDROID = "android";

    private static final String PREFS = "codepilotai.push";
    private static final String KEY_REGISTERED_TOKEN = "registered_token";

    private final DevicesApi api;
    private final SharedPreferences prefs;

    public DevicesRepository(DevicesApi api, Context context) {
        this.api = api;
        this.prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    /** Register only when the token is new or changed; a repeat is a no-op. */
    public Completable registerIfChanged(String token) {
        if (isBlank(token) || token.equals(registeredToken())) {
            return Completable.complete();
        }
        return register(token, PLATFORM_ANDROID);
    }

    public Completable register(String token, String platform) {
        if (isBlank(token)) {
            return Completable.complete();
        }
        return api.register(new DeviceTokenRequest(token, platform))
                .doOnSuccess(ignored -> remember(token))
                .ignoreElement()
                .onErrorResumeNext(t -> Completable.error(ApiException.from(t)));
    }

    /**
     * Forget this install's registration server-side, then locally. Failures are
     * swallowed: a failed unregister must never block sign-out, and the next
     * sign-in re-registers (and reassigns) whatever token the device has.
     */
    public Completable unregisterCurrent() {
        String token = registeredToken();
        if (isBlank(token)) {
            return Completable.complete();
        }
        return api.unregister(Collections.singletonMap("token", token))
                .doOnComplete(this::forget)
                .onErrorResumeNext(t -> Completable.fromAction(this::forget));
    }

    /** The token last accepted by the backend, or {@code null} if none. */
    public String registeredToken() {
        return prefs.getString(KEY_REGISTERED_TOKEN, null);
    }

    private void remember(String token) {
        prefs.edit().putString(KEY_REGISTERED_TOKEN, token).apply();
    }

    private void forget() {
        prefs.edit().remove(KEY_REGISTERED_TOKEN).apply();
    }

    private static boolean isBlank(String value) {
        return value == null || value.trim().isEmpty();
    }
}
