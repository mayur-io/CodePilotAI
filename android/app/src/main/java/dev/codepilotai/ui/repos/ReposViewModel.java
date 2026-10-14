package dev.codepilotai.ui.repos;

import androidx.lifecycle.LiveData;
import androidx.lifecycle.MutableLiveData;
import androidx.lifecycle.ViewModel;
import androidx.lifecycle.ViewModelProvider;
import dev.codepilotai.data.db.entity.RepositoryEntity;
import dev.codepilotai.data.repo.AuthRepository;
import dev.codepilotai.data.repo.ReposRepository;
import dev.codepilotai.util.Event;
import io.reactivex.rxjava3.android.schedulers.AndroidSchedulers;
import io.reactivex.rxjava3.disposables.CompositeDisposable;
import io.reactivex.rxjava3.schedulers.Schedulers;
import java.util.List;

/** Repository list screen: browse, search, create, sync, sign out. */
public final class ReposViewModel extends ViewModel {

    private final ReposRepository repos;
    private final AuthRepository auth;
    private final CompositeDisposable disposable = new CompositeDisposable();

    private final MutableLiveData<List<RepositoryEntity>> reposList = new MutableLiveData<>();
    private final MutableLiveData<Boolean> refreshing = new MutableLiveData<>(false);
    private final Event<String> error = new Event<>();
    private final Event<Long> opened = new Event<>();
    private final Event<Boolean> signedOut = new Event<>();

    public ReposViewModel(ReposRepository repos, AuthRepository auth) {
        this.repos = repos;
        this.auth = auth;
        disposable.add(
                repos.observeRepos()
                        .observeOn(AndroidSchedulers.mainThread())
                        .subscribe(
                                reposList::setValue,
                                t -> error.fire(t.getMessage())));
    }

    public LiveData<List<RepositoryEntity>> reposList() {
        return reposList;
    }

    public LiveData<Boolean> refreshing() {
        return refreshing;
    }

    public LiveData<String> errors() {
        return error.asLiveData();
    }

    public LiveData<Long> opened() {
        return opened.asLiveData();
    }

    public LiveData<Boolean> signedOut() {
        return signedOut.asLiveData();
    }

    public void load() {
        refreshing.setValue(true);
        disposable.add(
                repos.refresh()
                        .observeOn(AndroidSchedulers.mainThread())
                        .subscribe(
                                () -> refreshing.setValue(false),
                                t -> {
                                    refreshing.setValue(false);
                                    error.fire(t.getMessage());
                                }));
    }

    public void syncFromGithub() {
        refreshing.setValue(true);
        disposable.add(
                repos.syncFromGithub()
                        .observeOn(AndroidSchedulers.mainThread())
                        .subscribe(
                                () -> refreshing.setValue(false),
                                t -> {
                                    refreshing.setValue(false);
                                    error.fire(t.getMessage());
                                }));
    }

    public void create(String name, String description, boolean isPrivate) {
        disposable.add(
                repos.create(name, description, isPrivate)
                        .observeOn(AndroidSchedulers.mainThread())
                        .subscribe(
                                created -> opened.fire(created.id),
                                t -> error.fire(t.getMessage())));
    }

    public void open(RepositoryEntity repo) {
        repos.select(repo.id);
        opened.fire(repo.id);
    }

    public void signOut() {
        disposable.add(
                auth.logout()
                        .observeOn(AndroidSchedulers.mainThread())
                        .subscribe(
                                () -> signedOut.fire(true),
                                t -> signedOut.fire(true)));
    }

    public boolean isSignedIn() {
        return auth.isSignedIn();
    }

    public String userName() {
        return "";
    }

    @Override
    protected void onCleared() {
        disposable.clear();
    }

    /** Dagger-friendly factory. */
    public static final class Factory implements ViewModelProvider.Factory {
        private final ReposRepository repos;
        private final AuthRepository auth;

        public Factory(ReposRepository repos, AuthRepository auth) {
            this.repos = repos;
            this.auth = auth;
        }

        @SuppressWarnings("unchecked")
        @Override
        public <T extends ViewModel> T create(Class<T> modelClass) {
            return (T) new ReposViewModel(repos, auth);
        }
    }
}