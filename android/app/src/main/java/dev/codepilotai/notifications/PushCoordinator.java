package dev.codepilotai.notifications;

import android.annotation.SuppressLint;
import android.content.Context;
import dev.codepilotai.data.prefs.TokenStore;
import dev.codepilotai.data.repo.DevicesRepository;
import io.reactivex.rxjava3.schedulers.Schedulers;

/**
 * Best-effort wiring between Firebase Messaging and the backend.
 *
 * <p>Every entry point is fire-and-forget on the IO scheduler: registering a
 * push token must never surface an error to the user, block the UI, or fail a
 * sign-in. When Firebase is not configured, or the user is signed out, the
 * calls quietly do nothing.
 */
public final class PushCoordinator {

    private PushCoordinator() {
    }

    /** Register this install's token, if signed in and Firebase is configured. */
    @SuppressLint("CheckResult") // Fire-and-forget; the errors are deliberately dropped.
    public static void registerIfSignedIn(
            Context context, TokenStore tokenStore, DevicesRepository devices) {
        if (!tokenStore.hasSession() || !FirebasePush.isConfigured(context)) {
            return;
        }
        FirebasePush.currentToken(context)
                .flatMapCompletable(devices::registerIfChanged)
                .subscribeOn(Schedulers.io())
                .subscribe(() -> { }, error -> { });
    }

    /** Register a token Firebase just handed us, if a session is active. */
    @SuppressLint("CheckResult") // Fire-and-forget; the errors are deliberately dropped.
    public static void registerToken(
            Context context, TokenStore tokenStore, DevicesRepository devices, String token) {
        if (!tokenStore.hasSession() || token == null || token.trim().isEmpty()) {
            return;
        }
        devices.registerIfChanged(token)
                .subscribeOn(Schedulers.io())
                .subscribe(() -> { }, error -> { });
    }
}
