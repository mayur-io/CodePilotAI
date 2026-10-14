package dev.codepilotai.ui.result;

import androidx.lifecycle.LiveData;
import androidx.lifecycle.MutableLiveData;
import androidx.lifecycle.ViewModel;
import androidx.lifecycle.ViewModelProvider;
import dev.codepilotai.data.db.entity.EventEntity;
import dev.codepilotai.data.repo.TaskRepository;
import dev.codepilotai.domain.model.AgentTaskDetail;
import dev.codepilotai.domain.model.RunEvent;
import dev.codepilotai.util.AuditLog;
import dev.codepilotai.util.Event;
import io.reactivex.rxjava3.android.schedulers.AndroidSchedulers;
import io.reactivex.rxjava3.disposables.CompositeDisposable;
import io.reactivex.rxjava3.schedulers.Schedulers;
import java.util.ArrayList;
import java.util.List;

/** Result screen: summary, PR link, verification cards, token/cost stats. */
public final class ResultViewModel extends ViewModel {

    public static final class State {
        public final boolean loading;
        public final AgentTaskDetail detail;
        public final String error;

        private State(boolean loading, AgentTaskDetail detail, String error) {
            this.loading = loading;
            this.detail = detail;
            this.error = error;
        }

        public static State loading() {
            return new State(true, null, null);
        }

        public static State of(AgentTaskDetail detail) {
            return new State(false, detail, null);
        }

        public static State error(String message) {
            return new State(false, null, message);
        }
    }

    private final TaskRepository tasks;
    private final CompositeDisposable disposable = new CompositeDisposable();
    private final MutableLiveData<State> state = new MutableLiveData<>(State.loading());
    private final MutableLiveData<List<RunEvent>> auditEvents = new MutableLiveData<>();
    private final Event<String> openUrl = new Event<>();

    public ResultViewModel(TaskRepository tasks) {
        this.tasks = tasks;
    }

    public void load(long taskId) {
        disposable.add(
                tasks.detail(taskId)
                        .subscribeOn(Schedulers.io())
                        .observeOn(AndroidSchedulers.mainThread())
                        .subscribe(
                                detail -> state.setValue(State.of(detail)),
                                t -> state.setValue(State.error(t.getMessage()))));

        // Backfill the event cache so the audit trail is complete even when this
        // task was opened from history and never streamed in this install.
        disposable.add(
                tasks.eventsAfter(taskId, 0)
                        .ignoreElement()
                        .onErrorComplete()
                        .subscribeOn(Schedulers.io())
                        .subscribe());

        disposable.add(
                tasks.observeEvents(taskId)
                        .map(this::auditLines)
                        .observeOn(AndroidSchedulers.mainThread())
                        .subscribe(auditEvents::setValue, t -> { }));
    }

    private List<RunEvent> auditLines(List<EventEntity> entities) {
        List<RunEvent> events = new ArrayList<>();
        if (entities != null) {
            for (EventEntity entity : entities) {
                events.add(tasks.fromEventEntity(entity));
            }
        }
        return AuditLog.filtered(events);
    }

    public LiveData<State> state() {
        return state;
    }

    public LiveData<List<RunEvent>> auditEvents() {
        return auditEvents;
    }

    public LiveData<String> openUrl() {
        return openUrl.asLiveData();
    }

    public void openPullRequest(AgentTaskDetail detail) {
        if (detail.prUrl != null && !detail.prUrl.trim().isEmpty()) {
            openUrl.fire(detail.prUrl);
        }
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
            return (T) new ResultViewModel(tasks);
        }
    }
}