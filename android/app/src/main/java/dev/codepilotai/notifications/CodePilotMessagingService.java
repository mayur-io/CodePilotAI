package dev.codepilotai.notifications;

import androidx.annotation.NonNull;
import com.google.firebase.messaging.FirebaseMessagingService;
import com.google.firebase.messaging.RemoteMessage;
import dev.codepilotai.CodePilotApp;
import dev.codepilotai.R;
import java.util.Map;

/**
 * Entry point for FCM.
 *
 * <p>The backend sends data-only messages, so this always runs -- foreground,
 * background, or cold start -- and builds the notification itself (channel,
 * deep link), instead of letting the SDK post a bare one with no task id.
 */
public final class CodePilotMessagingService extends FirebaseMessagingService {

    @Override
    public void onNewToken(@NonNull String token) {
        super.onNewToken(token);
        CodePilotApp app = (CodePilotApp) getApplication();
        if (app == null) {
            return;
        }
        PushCoordinator.registerToken(
                this, app.component().tokenStore(), app.component().devicesRepository(), token);
    }

    @Override
    public void onMessageReceived(@NonNull RemoteMessage message) {
        super.onMessageReceived(message);
        Map<String, String> data = message.getData();
        if (data.isEmpty()) {
            return;
        }
        String title = valueOr(data.get("title"), getString(R.string.notification_channel_tasks));
        String body = valueOr(data.get("body"), "");
        String channel = valueOr(data.get("channel_id"), TaskNotifications.DEFAULT_CHANNEL_ID);
        TaskNotifications.show(this, channel, title, body, data.get("task_id"));
    }

    private static String valueOr(String value, String fallback) {
        return value == null || value.trim().isEmpty() ? fallback : value;
    }
}
