package dev.codepilotai.ui;

import android.Manifest;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.view.View;
import androidx.annotation.Nullable;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.app.ActivityCompat;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowInsetsCompat;
import androidx.lifecycle.ViewModelProvider;
import androidx.navigation.NavController;
import androidx.navigation.fragment.NavHostFragment;
import com.google.android.material.color.DynamicColors;
import dev.codepilotai.CodePilotApp;
import dev.codepilotai.R;
import dev.codepilotai.domain.model.TaskStatus;
import dev.codepilotai.notifications.PushCoordinator;
import dev.codepilotai.ui.auth.AuthViewModel;
import dev.codepilotai.work.DraftSyncWorker;

/**
 * Single-activity shell. Owns the activity-scoped {@link AuthViewModel} so the
 * {@code codepilotai://auth/callback} deep link can be exchanged on any screen.
 */
public final class MainActivity extends AppCompatActivity {

    /** Extra carrying a task id from a notification tap. */
    public static final String EXTRA_TASK_ID = "extra_task_id";

    /** Extra carrying the task status a notification tap was about. */
    public static final String EXTRA_TASK_STATUS = "extra_task_status";

    private AuthViewModel authViewModel;
    private NavController navController;
    private String pendingTaskId;
    private String pendingTaskStatus;

    @Override
    protected void onCreate(@Nullable Bundle savedInstanceState) {
        DynamicColors.applyToActivityIfAvailable(this);
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        applySystemBarInsets();

        CodePilotApp app = (CodePilotApp) getApplication();
        authViewModel =
                new ViewModelProvider(this, app.component().authViewModelFactory())
                        .get(AuthViewModel.class);

        NavHostFragment host =
                (NavHostFragment) getSupportFragmentManager().findFragmentById(R.id.navHostFragment);
        if (host != null) {
            navController = host.getNavController();
        }

        // Recurring worker for the offline task-draft queue (idempotent).
        DraftSyncWorker.schedule(this);

        // Cold start from a deep link or a notification tap.
        handleAuthUri(getIntent());
        pendingTaskId = taskIdFrom(getIntent());
        pendingTaskStatus = taskStatusFrom(getIntent());

        // Push registration and the runtime notification permission both wait
        // for a session, so they run off the auth state (cold start included).
        authViewModel.state().observe(this, state -> {
            if (state != null && state.signedIn) {
                onSignedIn();
            }
        });
    }

    /**
     * Pads the NavHost with the system-bar and IME insets. The app targets SDK 36, so the
     * platform draws it edge-to-edge behind the status and navigation bars; without this the
     * topmost toolbar of every destination would sit under the status bar and the bottom-most
     * action (FAB, decision bar, CTA) under the navigation bar. Padding the host once here keeps
     * all destinations correct without editing seven layouts or double-padding them, because no
     * fragment manages insets itself. The bottom padding is the larger of the navigation bar and
     * the keyboard so an on-screen IME never hides a text field or its submit button.
     */
    private void applySystemBarInsets() {
        View root = findViewById(R.id.navHostFragment);
        ViewCompat.setOnApplyWindowInsetsListener(
                root,
                (view, windowInsets) -> {
                    Insets systemBars =
                            windowInsets.getInsets(WindowInsetsCompat.Type.systemBars());
                    Insets ime = windowInsets.getInsets(WindowInsetsCompat.Type.ime());
                    view.setPadding(
                            systemBars.left,
                            systemBars.top,
                            systemBars.right,
                            Math.max(systemBars.bottom, ime.bottom));
                    // Consume nothing so child screens can still query insets if needed.
                    return windowInsets;
                });
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        // Warm start (singleTask) from a deep link or a notification tap.
        handleAuthUri(intent);
        pendingTaskId = taskIdFrom(intent);
        pendingTaskStatus = taskStatusFrom(intent);
        if (authViewModel.isSignedIn()) {
            onSignedIn();
        }
    }

    /** Signed-in side effects: register for push, ask for notifications, route. */
    private void onSignedIn() {
        CodePilotApp app = (CodePilotApp) getApplication();
        PushCoordinator.registerIfSignedIn(
                this, app.component().tokenStore(), app.component().devicesRepository());
        maybeRequestNotifications();
        if (pendingTaskId != null) {
            String taskId = pendingTaskId;
            String status = pendingTaskStatus;
            pendingTaskId = null;
            pendingTaskStatus = null;
            navigateToTask(taskId, status);
        }
    }

    private void maybeRequestNotifications() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) {
            return;
        }
        if (ActivityCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                == PackageManager.PERMISSION_GRANTED) {
            return;
        }
        ActivityCompat.requestPermissions(
                this, new String[] {Manifest.permission.POST_NOTIFICATIONS}, 4001);
    }

    private void navigateToTask(String taskId, @Nullable String status) {
        if (navController == null) {
            return;
        }
        long id;
        try {
            id = Long.parseLong(taskId);
        } catch (NumberFormatException e) {
            return;
        }
        Bundle args = new Bundle();
        args.putLong("taskId", id);
        // A "needs your answers" tap goes straight to the questions; anything
        // else opens the run screen, which forwards to clarify/approval once the
        // stream reports the relevant status.
        if (TaskStatus.CLARIFYING.wire().equals(status)) {
            navController.navigate(R.id.clarifyFragment, args);
        } else {
            navController.navigate(R.id.runFragment, args);
        }
    }

    @Nullable
    private static String taskIdFrom(@Nullable Intent intent) {
        return intent == null ? null : intent.getStringExtra(EXTRA_TASK_ID);
    }

    @Nullable
    private static String taskStatusFrom(@Nullable Intent intent) {
        return intent == null ? null : intent.getStringExtra(EXTRA_TASK_STATUS);
    }

    private void handleAuthUri(@Nullable Intent intent) {
        if (intent == null || intent.getData() == null) {
            return;
        }
        Uri data = intent.getData();
        if (!"codepilotai".equals(data.getScheme()) || !"auth".equals(data.getHost())) {
            return;
        }
        String error = data.getQueryParameter("error");
        if (error != null) {
            String description = data.getQueryParameter("error_description");
            android.widget.Toast.makeText(
                            this,
                            description == null ? error : description,
                            android.widget.Toast.LENGTH_LONG)
                    .show();
            return;
        }
        String code = data.getQueryParameter("code");
        if (code != null) {
            authViewModel.handleCode(code);
            // AuthFragment navigates when the exchange lands on the auth screen;
            // only steer from here if the user was mid-session somewhere else.
            authViewModel.signedIn().observe(
                    this,
                    signedIn -> {
                        if (signedIn != null && signedIn && navController != null) {
                            navigateToRepos();
                        }
                    });
        }
    }

    private void navigateToRepos() {
        int current = navController.getCurrentDestination() == null
                ? -1
                : navController.getCurrentDestination().getId();
        if (current == R.id.authFragment) {
            // The auth fragment handles this navigation.
            return;
        }
        if (current != R.id.reposFragment) {
            navController.navigate(R.id.reposFragment);
        }
    }

    /** Intent that opens the run (or clarify) screen for {@code taskId} when tapped. */
    public static Intent taskIntent(
            Context context, @Nullable String taskId, @Nullable String status) {
        Intent intent = new Intent(context, MainActivity.class);
        intent.setFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        if (taskId != null && !taskId.trim().isEmpty()) {
            intent.putExtra(EXTRA_TASK_ID, taskId);
        }
        if (status != null && !status.trim().isEmpty()) {
            intent.putExtra(EXTRA_TASK_STATUS, status);
        }
        return intent;
    }

    /** Open a URL in the system browser or Custom Tab. */
    public static void openUrl(AppCompatActivity activity, String url) {
        if (url == null || url.trim().isEmpty()) {
            return;
        }
        Intent intent = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
        activity.startActivity(intent);
    }
}