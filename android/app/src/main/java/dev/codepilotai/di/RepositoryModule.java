package dev.codepilotai.di;

import android.content.Context;
import com.squareup.moshi.Moshi;
import dagger.Module;
import dagger.Provides;
import dev.codepilotai.data.api.AuthApi;
import dev.codepilotai.data.api.DevicesApi;
import dev.codepilotai.data.api.ReposApi;
import dev.codepilotai.data.api.SseClient;
import dev.codepilotai.data.api.TasksApi;
import dev.codepilotai.data.db.AppDatabase;
import dev.codepilotai.data.prefs.TokenStore;
import dev.codepilotai.data.repo.AuthRepository;
import dev.codepilotai.data.repo.DevicesRepository;
import dev.codepilotai.data.repo.DraftRepository;
import dev.codepilotai.data.repo.ReposRepository;
import dev.codepilotai.data.repo.TaskRepository;
import dev.codepilotai.work.CodePilotWorkerFactory;
import javax.inject.Singleton;

/** Wires the repositories (and the worker's dependencies) into the graph. */
@Module
public final class RepositoryModule {

    @Provides
    @Singleton
    static AuthRepository provideAuthRepository(
            AuthApi api, TokenStore tokenStore, DevicesRepository devices) {
        return new AuthRepository(api, tokenStore, devices);
    }

    @Provides
    @Singleton
    static DevicesRepository provideDevicesRepository(DevicesApi api, Context context) {
        return new DevicesRepository(api, context);
    }

    @Provides
    @Singleton
    static ReposRepository provideReposRepository(ReposApi api, AppDatabase db) {
        return new ReposRepository(api, db);
    }

    @Provides
    @Singleton
    static TaskRepository provideTaskRepository(
            TasksApi api, SseClient sse, AppDatabase db, Moshi moshi) {
        return new TaskRepository(api, sse, db, moshi);
    }

    @Provides
    @Singleton
    static DraftRepository provideDraftRepository(
            TasksApi api, AppDatabase db, TaskRepository taskRepository) {
        return new DraftRepository(api, db, taskRepository);
    }

    @Provides
    @Singleton
    static CodePilotWorkerFactory provideWorkerFactory(DraftRepository drafts) {
        return new CodePilotWorkerFactory(drafts);
    }
}