package dev.codepilotai.ui.approval;

import androidx.lifecycle.LiveData;
import androidx.lifecycle.MutableLiveData;
import androidx.lifecycle.ViewModel;
import androidx.lifecycle.ViewModelProvider;
import dev.codepilotai.data.db.entity.ChangeEntity;
import dev.codepilotai.data.repo.TaskRepository;
import dev.codepilotai.domain.model.ChangeStatus;
import dev.codepilotai.domain.model.ProposedChange;
import dev.codepilotai.util.Event;
import io.reactivex.rxjava3.android.schedulers.AndroidSchedulers;
import io.reactivex.rxjava3.disposables.CompositeDisposable;
import io.reactivex.rxjava3.schedulers.Schedulers;
import java.util.ArrayList;
import java.util.List;

/**
 * Approval screen (Task.md §9): per-change approve / reject / request-edit,
 * thumb-reachable bottom bar, then a single "apply approved changes" action.
 */
public final class ApprovalViewModel extends ViewModel {

    private final TaskRepository tasks;
    private final CompositeDisposable disposable = new CompositeDisposable();

    private final MutableLiveData<List<ProposedChange>> changes = new MutableLiveData<>(new ArrayList<>());
    private final MutableLiveData<Boolean> busy = new MutableLiveData<>(false);
    private final MutableLiveData<Boolean> pendingExists = new MutableLiveData<>(false);
    private final MutableLiveData<Boolean> canExecute = new MutableLiveData<>(false);
    private final Event<Long> executed = new Event<>();
    private final Event<String> error = new Event<>();

    private long taskId;

    public ApprovalViewModel(TaskRepository tasks) {
        this.tasks = tasks;
    }

    public void start(long taskId) {
        this.taskId = taskId;
        disposable.add(
                tasks.observeChanges(taskId)
                        .observeOn(AndroidSchedulers.mainThread())
                        .subscribe(
                                this::onCachedChanges,
                                t -> error.fire(t.getMessage())));
        refresh();
    }

    private void onCachedChanges(List<ChangeEntity> entities) {
        List<ProposedChange> mapped = new ArrayList<>();
        for (ChangeEntity e : entities) {
            mapped.add(TaskRepository.toDomain(e));
        }
        publish(mapped);
    }

    private void publish(List<ProposedChange> list) {
        changes.setValue(list);
        boolean pending = false;
        boolean executedAll = list.size() > 0;
        boolean anyApproved = false;
        for (ProposedChange c : list) {
            if (c.statusEnum() == ChangeStatus.PENDING) {
                pending = true;
            } else if (c.statusEnum() == ChangeStatus.APPROVED) {
                anyApproved = true;
            }
        }
        pendingExists.setValue(pending);
        // Execute is enabled once at least one change is approved and nothing
        // is still awaiting a decision.
        canExecute.setValue(anyApproved && !pending);
    }

    public void refresh() {
        disposable.add(
                tasks.changes(taskId)
                        .subscribeOn(Schedulers.io())
                        .observeOn(AndroidSchedulers.mainThread())
                        .subscribe(
                                this::publish,
                                t -> error.fire(t.getMessage())));
    }

    public void decide(long changeId, String decision, String feedback) {
        busy.setValue(true);
        disposable.add(
                tasks.decide(taskId, changeId, decision, feedback)
                        .observeOn(AndroidSchedulers.mainThread())
                        .subscribe(
                                updated -> {
                                    busy.setValue(false);
                                    refresh();
                                },
                                t -> {
                                    busy.setValue(false);
                                    error.fire(t.getMessage());
                                }));
    }

    /** Start execution: apply approved changes, then run verify/commit/push. */
    public void execute() {
        busy.setValue(true);
        disposable.add(
                tasks.execute(taskId)
                        .observeOn(AndroidSchedulers.mainThread())
                        .subscribe(
                                detail -> {
                                    busy.setValue(false);
                                    executed.fire(taskId);
                                },
                                t -> {
                                    busy.setValue(false);
                                    error.fire(t.getMessage());
                                }));
    }

    public LiveData<List<ProposedChange>> changes() {
        return changes;
    }

    public LiveData<Boolean> busy() {
        return busy;
    }

    public LiveData<Boolean> pendingExists() {
        return pendingExists;
    }

    public LiveData<Boolean> canExecute() {
        return canExecute;
    }

    public LiveData<Long> executed() {
        return executed.asLiveData();
    }

    public LiveData<String> errors() {
        return error.asLiveData();
    }

    @Override
    protected void onCleared() {
        disposable.clear();
    }

    /** Dagger-friendly factory. */
    public static final class Factory implements ViewModelProvider.Factory {
        private final TaskRepository tasks;

        public Factory(TaskRepository tasks) {
            this.tasks = tasks;
        }

        @SuppressWarnings("unchecked")
        @Override
        public <T extends ViewModel> T create(Class<T> modelClass) {
            return (T) new ApprovalViewModel(tasks);
        }
    }
}