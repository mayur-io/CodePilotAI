package dev.codepilotai.ui.task;

import androidx.lifecycle.LiveData;
import androidx.lifecycle.MutableLiveData;
import androidx.lifecycle.ViewModel;
import androidx.lifecycle.ViewModelProvider;
import dev.codepilotai.data.db.entity.RepositoryEntity;
import dev.codepilotai.data.repo.DraftRepository;
import dev.codepilotai.data.repo.ReposRepository;
import dev.codepilotai.data.repo.TaskRepository;
import dev.codepilotai.util.Event;
import io.reactivex.rxjava3.android.schedulers.AndroidSchedulers;
import io.reactivex.rxjava3.disposables.CompositeDisposable;
import io.reactivex.rxjava3.schedulers.Schedulers;
import java.util.List;

/** New-task form: pick a repo, write the prompt, send (or queue offline). */
public final class NewTaskViewModel extends ViewModel {

    public enum SubmitState {
        IDLE, SENDING, SAVED_OFFLINE, CREATED
    }

    private final TaskRepository tasks;
    private final DraftRepository drafts;
    private final ReposRepository repos;
    private final CompositeDisposable disposable = new CompositeDisposable();

    private final MutableLiveData<List<RepositoryEntity>> repoList = new MutableLiveData<>();
    private final MutableLiveData<Long> selectedRepoId = new MutableLiveData<>();
    private final MutableLiveData<SubmitState> submitState = new MutableLiveData<>(SubmitState.IDLE);
    private final Event<Long> created = new Event<>();
    private final Event<String> error = new Event<>();
    private final Event<String> notice = new Event<>();

    public NewTaskViewModel(
            TaskRepository tasks, DraftRepository drafts, ReposRepository repos) {
        this.tasks = tasks;
        this.drafts = drafts;
        this.repos = repos;
        disposable.add(
                repos.observeRepos()
                        .observeOn(AndroidSchedulers.mainThread())
                        .subscribe(repoList::setValue, t -> error.fire(t.getMessage())));
    }

    public LiveData<List<RepositoryEntity>> repoList() {
        return repoList;
    }

    public LiveData<Long> selectedRepoId() {
        return selectedRepoId;
    }

    public LiveData<SubmitState> submitState() {
        return submitState;
    }

    public LiveData<Long> createdTaskId() {
        return created.asLiveData();
    }

    public LiveData<String> errors() {
        return error.asLiveData();
    }

    public LiveData<String> notices() {
        return notice.asLiveData();
    }

    public void selectRepository(long id) {
        selectedRepoId.setValue(id);
        repos.select(id);
    }

    public void load() {
        disposable.add(
                repos.refresh()
                        .subscribe(
                                () -> {
                                },
                                t -> error.fire(t.getMessage())));
    }

    /** Send the task; on a network failure persist it for the worker instead. */
    public void submit(String prompt, String baseBranch, String title) {
        Long repoId = selectedRepoId.getValue();
        if (repoId == null || prompt == null || prompt.trim().isEmpty()) {
            error.fire("Pick a repository and describe the task first.");
            return;
        }
        submitState.setValue(SubmitState.SENDING);
        String repoName = currentRepoName(repoId);
        disposable.add(
                tasks.create(
                                new dev.codepilotai.data.api.dto.CreateTaskRequest(
                                        repoId, prompt, baseBranch, title))
                        .subscribeOn(Schedulers.io())
                        .observeOn(AndroidSchedulers.mainThread())
                        .subscribe(
                                detail -> {
                                    submitState.setValue(SubmitState.CREATED);
                                    created.fire(detail.id);
                                },
                                t -> saveOffline(
                                        repoId, repoName, prompt, baseBranch, title)));
    }

    private String currentRepoName(long repoId) {
        List<RepositoryEntity> list = repoList.getValue();
        if (list == null) {
            return "";
        }
        for (RepositoryEntity r : list) {
            if (r.id == repoId) {
                return r.fullName;
            }
        }
        return "";
    }

    private void saveOffline(
            long repoId, String repoName, String prompt, String baseBranch, String title) {
        disposable.add(
                drafts.saveDraft(repoId, repoName, prompt, baseBranch, title)
                        .subscribeOn(Schedulers.io())
                        .observeOn(AndroidSchedulers.mainThread())
                        .subscribe(
                                id -> {
                                    submitState.setValue(SubmitState.SAVED_OFFLINE);
                                    notice.fire("Saved offline — will send when you're back online.");
                                },
                                t -> {
                                    submitState.setValue(SubmitState.IDLE);
                                    error.fire(t.getMessage());
                                }));
    }

    @Override
    protected void onCleared() {
        disposable.clear();
    }

    /** Dagger-friendly factory. */
    public static final class Factory implements ViewModelProvider.Factory {
        private final TaskRepository tasks;
        private final DraftRepository drafts;
        private final ReposRepository repos;

        public Factory(TaskRepository tasks, DraftRepository drafts, ReposRepository repos) {
            this.tasks = tasks;
            this.drafts = drafts;
            this.repos = repos;
        }

        @SuppressWarnings("unchecked")
        @Override
        public <T extends ViewModel> T create(Class<T> modelClass) {
            return (T) new NewTaskViewModel(tasks, drafts, repos);
        }
    }
}