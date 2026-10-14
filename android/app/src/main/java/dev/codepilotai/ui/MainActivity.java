package dev.codepilotai.ui;

import android.Manifest;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import androidx.annotation.Nullable;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.app.ActivityCompat;
import androidx.lifecycle.ViewModelProvider;
import androidx.navigation.NavController;
import androidx.navigation.fragment.NavHostFragment;
import com.google.android.material.color.DynamicColors;
import dev.codepilotai.CodePilotApp;
import dev.codepilotai.R;
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

    private AuthViewModel authViewModel;
    private NavController navController;
    private String pendingTaskId;

    @Override
    protected void onCreate(@Nullable Bundle savedInstanceState) {
        DynamicColors.applyToActivityIfAvailable(this);
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

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

        // Push registration and the runtime notification permission both wait
        // for a session, so they run off the auth state (cold start included).
        authViewModel.state().observe(this, state -> {
            if (state != null && state.signedIn) {
                onSignedIn();
            }
        });
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        // Warm start (singleTask) from a deep link or a notification tap.
        handleAuthUri(intent);
        pendingTaskId = taskIdFrom(intent);
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
            pendingTaskId = null;
            navigateToTask(taskId);
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

    private void navigateToTask(String taskId) {
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
        navController.navigate(R.id.runFragment, args);
    }

    @Nullable
    private static String taskIdFrom(@Nullable Intent intent) {
        return intent == null ? null : intent.getStringExtra(EXTRA_TASK_ID);
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

    /** Intent that opens the run screen for {@code taskId} when tapped. */
    public static Intent taskIntent(Context context, @Nullable String taskId) {
        Intent intent = new Intent(context, MainActivity.class);
        intent.setFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        if (taskId != null && !taskId.trim().isEmpty()) {
            intent.putExtra(EXTRA_TASK_ID, taskId);
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