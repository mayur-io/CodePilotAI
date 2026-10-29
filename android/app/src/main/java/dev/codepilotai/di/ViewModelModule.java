package dev.codepilotai.di;

import dagger.Module;
import dagger.Provides;
import dev.codepilotai.data.repo.AuthRepository;
import dev.codepilotai.data.repo.DraftRepository;
import dev.codepilotai.data.repo.ReposRepository;
import dev.codepilotai.data.repo.TaskRepository;
import dev.codepilotai.ui.approval.ApprovalViewModel;
import dev.codepilotai.ui.auth.AuthViewModel;
import dev.codepilotai.ui.clarify.ClarifyViewModel;
import dev.codepilotai.ui.history.HistoryViewModel;
import dev.codepilotai.ui.repos.ReposViewModel;
import dev.codepilotai.ui.result.ResultViewModel;
import dev.codepilotai.ui.run.RunViewModel;
import dev.codepilotai.ui.task.NewTaskViewModel;
import javax.inject.Singleton;

@Module
public final class ViewModelModule {

    @Provides
    @Singleton
    static AuthViewModel.Factory authFactory(AuthRepository auth) {
        return new AuthViewModel.Factory(auth);
    }

    @Provides
    @Singleton
    static ReposViewModel.Factory reposFactory(ReposRepository repos, AuthRepository auth) {
        return new ReposViewModel.Factory(repos, auth);
    }

    @Provides
    @Singleton
    static NewTaskViewModel.Factory newTaskFactory(
            TaskRepository tasks, DraftRepository drafts, ReposRepository repos) {
        return new NewTaskViewModel.Factory(tasks, drafts, repos);
    }

    @Provides
    @Singleton
    static RunViewModel.Factory runFactory(TaskRepository tasks) {
        return new RunViewModel.Factory(tasks);
    }

    @Provides
    @Singleton
    static ApprovalViewModel.Factory approvalFactory(TaskRepository tasks) {
        return new ApprovalViewModel.Factory(tasks);
    }

    @Provides
    @Singleton
    static ClarifyViewModel.Factory clarifyFactory(TaskRepository tasks) {
        return new ClarifyViewModel.Factory(tasks);
    }

    @Provides
    @Singleton
    static ResultViewModel.Factory resultFactory(TaskRepository tasks) {
        return new ResultViewModel.Factory(tasks);
    }

    @Provides
    @Singleton
    static HistoryViewModel.Factory historyFactory(TaskRepository tasks) {
        return new HistoryViewModel.Factory(tasks);
    }
}