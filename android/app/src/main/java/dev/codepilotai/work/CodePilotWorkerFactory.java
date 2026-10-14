package dev.codepilotai.work;

import android.content.Context;
import androidx.annotation.NonNull;
import androidx.work.ListenableWorker;
import androidx.work.WorkerFactory;
import androidx.work.WorkerParameters;
import dev.codepilotai.data.repo.DraftRepository;

/** Resolves workers from the Dagger graph instead of reflection. */
public final class CodePilotWorkerFactory extends WorkerFactory {

    private final DraftRepository drafts;

    public CodePilotWorkerFactory(DraftRepository drafts) {
        this.drafts = drafts;
    }

    @Override
    public ListenableWorker createWorker(
            @NonNull Context appContext,
            @NonNull String workerClassName,
            @NonNull WorkerParameters workerParameters) {
        if (DraftSyncWorker.class.getName().equals(workerClassName)) {
            return new DraftSyncWorker(appContext, workerParameters, drafts);
        }
        return null;
    }
}