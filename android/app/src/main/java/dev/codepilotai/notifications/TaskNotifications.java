package dev.codepilotai.notifications;

import android.annotation.SuppressLint;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import androidx.annotation.Nullable;
import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;
import dev.codepilotai.R;
import dev.codepilotai.ui.MainActivity;

/** Builds and posts the "task update" notification for a data-only FCM message. */
public final class TaskNotifications {

    /** Must match {@code apps.notifications.push.ANDROID_CHANNEL_ID} on the server. */
    public static final String DEFAULT_CHANNEL_ID = "codepilot_tasks";

    private static final int NOTIFICATION_ID = 4001;

    private TaskNotifications() {
    }

    /**
     * Show a task-update notification. Tapping it opens {@link MainActivity} with
     * the task id, which routes to the run screen (or straight to the clarify
     * screen when the agent is waiting for answers).
     */
    @SuppressLint("MissingPermission") // Guarded by areNotificationsEnabled().
    public static void show(
            Context context,
            String channelId,
            String title,
            String body,
            @Nullable String taskId,
            @Nullable String status) {
        String channel = channelId == null || channelId.trim().isEmpty() ? DEFAULT_CHANNEL_ID : channelId;
        ensureChannel(context, channel);

        NotificationManagerCompat manager = NotificationManagerCompat.from(context);
        if (!manager.areNotificationsEnabled()) {
            return;
        }

        Intent intent = MainActivity.taskIntent(context, taskId, status);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE;
        PendingIntent pending = PendingIntent.getActivity(context, NOTIFICATION_ID, intent, flags);

        Notification notification =
                new NotificationCompat.Builder(context, channel)
                        .setSmallIcon(R.drawable.ic_notification)
                        .setContentTitle(title)
                        .setContentText(body)
                        .setStyle(new NotificationCompat.BigTextStyle().bigText(body))
                        .setContentIntent(pending)
                        .setAutoCancel(true)
                        .setPriority(NotificationCompat.PRIORITY_DEFAULT)
                        .build();
        try {
            manager.notify(NOTIFICATION_ID, notification);
        } catch (SecurityException e) {
            // Permission revoked between the check above and the post.
        }
    }

    private static void ensureChannel(Context context, String channelId) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return;
        }
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        if (manager == null || manager.getNotificationChannel(channelId) != null) {
            return;
        }
        NotificationChannel channel =
                new NotificationChannel(
                        channelId,
                        context.getString(R.string.notification_channel_tasks),
                        NotificationManager.IMPORTANCE_DEFAULT);
        channel.setDescription(context.getString(R.string.notification_channel_tasks_description));
        manager.createNotificationChannel(channel);
    }
}
