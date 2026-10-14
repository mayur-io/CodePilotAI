package dev.codepilotai.notifications;

import android.content.Context;
import com.google.firebase.FirebaseApp;
import com.google.firebase.messaging.FirebaseMessaging;
import io.reactivex.rxjava3.core.Maybe;

/**
 * Null-safe wrapper over Firebase Messaging.
 *
 * <p>Firebase is only initialized once {@code app/google-services.json} is
 * present, so every call here treats "not configured" as a normal, quiet
 * outcome rather than an error. That keeps a clean checkout (no Firebase
 * project) fully buildable and runnable, with push simply switched off.
 */
public final class FirebasePush {

    private FirebasePush() {
    }

    /** Whether a default Firebase app was initialized from google-services.json. */
    public static boolean isConfigured(Context context) {
        try {
            return !FirebaseApp.getApps(context).isEmpty();
        } catch (RuntimeException e) {
            return false;
        }
    }

    /** Emits the current FCM registration token, or completes empty if unavailable. */
    public static Maybe<String> currentToken(Context context) {
        if (!isConfigured(context)) {
            return Maybe.empty();
        }
        return Maybe.create(emitter -> {
            try {
                FirebaseMessaging.getInstance()
                        .getToken()
                        .addOnCompleteListener(task -> {
                            if (emitter.isDisposed()) {
                                return;
                            }
                            String token = task.isSuccessful() ? task.getResult() : null;
                            if (token == null) {
                                emitter.onComplete();
                            } else {
                                emitter.onSuccess(token);
                            }
                        });
            } catch (RuntimeException e) {
                if (!emitter.isDisposed()) {
                    emitter.onComplete();
                }
            }
        });
    }
}
