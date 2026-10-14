package dev.codepilotai.di;

import dagger.BindsInstance;
import dagger.Component;
import dev.codepilotai.CodePilotApp;
import dev.codepilotai.data.prefs.TokenStore;
import dev.codepilotai.data.repo.DevicesRepository;
import dev.codepilotai.ui.approval.ApprovalViewModel;
import dev.codepilotai.ui.auth.AuthViewModel;
import dev.codepilotai.ui.history.HistoryViewModel;
import dev.codepilotai.ui.repos.ReposViewModel;
import dev.codepilotai.ui.run.RunViewModel;
import dev.codepilotai.ui.result.ResultViewModel;
import dev.codepilotai.ui.task.NewTaskViewModel;
import dev.codepilotai.work.CodePilotWorkerFactory;
import javax.inject.Singleton;

@Singleton
@Component(modules = {AppModule.class, NetworkModule.class, DatabaseModule.class, RepositoryModule.class, ViewModelModule.class})
public interface AppComponent {

    void inject(CodePilotApp app);

    @Component.Builder
    interface Builder {
        @BindsInstance
        Builder application(android.app.Application application);

        AppComponent build();
    }

    CodePilotWorkerFactory workerFactory();

    TokenStore tokenStore();

    DevicesRepository devicesRepository();

    AuthViewModel.Factory authViewModelFactory();
    ReposViewModel.Factory reposViewModelFactory();
    NewTaskViewModel.Factory newTaskViewModelFactory();
    RunViewModel.Factory runViewModelFactory();
    ApprovalViewModel.Factory approvalViewModelFactory();
    ResultViewModel.Factory resultViewModelFactory();
    HistoryViewModel.Factory historyViewModelFactory();
}