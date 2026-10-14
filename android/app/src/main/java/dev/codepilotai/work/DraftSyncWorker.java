package dev.codepilotai.work;

import android.content.Context;
import androidx.annotation.NonNull;
import androidx.work.Constraints;
import androidx.work.Data;
import androidx.work.ExistingPeriodicWorkPolicy;
import androidx.work.NetworkType;
import androidx.work.PeriodicWorkRequest;
import androidx.work.WorkManager;
import androidx.work.Worker;
import androidx.work.WorkerParameters;
import dev.codepilotai.data.repo.DraftRepository;
import java.util.concurrent.TimeUnit;

/**
 * Offline queue (Task.md §9): task drafts persist in Room the moment the user
 * taps "Start" offline; this worker pushes them whenever the network is back.
 */
public final class DraftSyncWorker extends Worker {

    public static final String NAME = "draft-sync";

    private final DraftRepository drafts;

    public DraftSyncWorker(
            @NonNull Context context, @NonNull WorkerParameters params, DraftRepository drafts) {
        super(context, params);
        this.drafts = drafts;
    }

    @NonNull
    @Override
    public Result doWork() {
        try {
            int synced = drafts.syncPending().blockingGet();
            return Result.success(new Data.Builder().putInt("synced", synced).build());
        } catch (Exception e) {
            return Result.retry();
        }
    }

    /** Enqueue the recurring network-gated sync. */
    public static void schedule(Context context) {
        Constraints constraints =
                new Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build();
        PeriodicWorkRequest request =
                new PeriodicWorkRequest.Builder(DraftSyncWorker.class, 15, TimeUnit.MINUTES)
                        .setConstraints(constraints)
                        .build();
        WorkManager.getInstance(context)
                .enqueueUniquePeriodicWork(NAME, ExistingPeriodicWorkPolicy.KEEP, request);
    }
}