package dev.codepilotai.ui.run;

import androidx.lifecycle.LiveData;
import androidx.lifecycle.MutableLiveData;
import androidx.lifecycle.ViewModel;
import androidx.lifecycle.ViewModelProvider;
import dev.codepilotai.R;
import dev.codepilotai.data.db.entity.EventEntity;
import dev.codepilotai.data.repo.TaskRepository;
import dev.codepilotai.domain.model.RunEvent;
import dev.codepilotai.domain.model.TaskPhase;
import dev.codepilotai.domain.model.TaskStatus;
import dev.codepilotai.util.Event;
import dev.codepilotai.util.Labels;
import io.reactivex.rxjava3.android.schedulers.AndroidSchedulers;
import io.reactivex.rxjava3.disposables.CompositeDisposable;
import io.reactivex.rxjava3.schedulers.Schedulers;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.TimeUnit;

/**
 * Streaming run screen (Task.md §9): replays cached events, then follows the
 * SSE stream with bounded reconnect. Emits one-shot navigation signals for
 * {@code awaiting_approval} and terminal states.
 */
public final class RunViewModel extends ViewModel {

    private final TaskRepository tasks;
    private final CompositeDisposable disposable = new CompositeDisposable();

    private final MutableLiveData<List<RunEvent>> events = new MutableLiveData<>(new ArrayList<>());
    private final MutableLiveData<Integer> reasoningCount = new MutableLiveData<>(0);
    private final MutableLiveData<Integer> phaseRes = new MutableLiveData<>(R.string.phase_waiting);
    private final MutableLiveData<Boolean> connected = new MutableLiveData<>(false);
    private final Event<Long> approvalRequired = new Event<>();
    private final Event<String> terminalReached = new Event<>();

    private long taskId;
    private int reconnectAttempts;
    private boolean terminalFired;
    private boolean approvalFired;

    public RunViewModel(TaskRepository tasks) {
        this.tasks = tasks;
    }

    public LiveData<List<RunEvent>> events() {
        return events;
    }

    public LiveData<Integer> reasoningCount() {
        return reasoningCount;
    }

    public LiveData<Integer> phaseRes() {
        return phaseRes;
    }

    public LiveData<Boolean> connected() {
        return connected;
    }

    public LiveData<Long> approvalRequired() {
        return approvalRequired.asLiveData();
    }

    public LiveData<String> terminalReached() {
        return terminalReached.asLiveData();
    }

    public void start(long taskId) {
        this.taskId = taskId;
        approvalFired = false;
        terminalFired = false;
        // Replay whatever is cached from a previous visit, then go live.
        disposable.add(
                tasks.observeEvents(taskId)
                        .observeOn(AndroidSchedulers.mainThread())
                        .subscribe(
                                entities -> {
                                    List<RunEvent> mapped = new ArrayList<>();
                                    for (EventEntity e : entities) {
                                        mapped.add(tasks.fromEventEntity(e));
                                    }
                                    if (!mapped.isEmpty()) {
                                        applyEvents(mapped);
                                    }
                                },
                                t -> { }));
        connect();
    }

    private void connect() {
        disposable.add(
                tasks.cachedMaxSeq(taskId)
                        .flatMapPublisher(newSeq -> tasks.stream(taskId, newSeq))
                        .observeOn(AndroidSchedulers.mainThread())
                        .subscribe(
                                this::onEvent,
                                this::onStreamFailure,
                                this::onStreamClosed));
    }

    private void onEvent(RunEvent event) {
        reconnectAttempts = 0;
        connected.setValue(true);
        List<RunEvent> current = new ArrayList<>(events.getValue() == null
                ? new ArrayList<>() : events.getValue());
        current.add(event);
        applyEvents(current);
    }

    private void applyEvents(List<RunEvent> list) {
        events.setValue(list);
        int reasoning = 0;
        int phaseLabel = R.string.phase_working;
        for (RunEvent e : list) {
            if (RunEvent.Kind.REASONING == e.kindEnum()) {
                reasoning++;
            } else if (RunEvent.Kind.PHASE == e.kindEnum() && e.payload != null) {
                Object raw = e.payload.get("phase");
                if (raw != null) {
                    TaskPhase p = TaskPhase.fromWire(String.valueOf(raw));
                    if (p != null) {
                        phaseLabel = Labels.phaseLabelRes(p);
                    }
                }
            } else if (RunEvent.Kind.STATUS == e.kindEnum() && e.payload != null) {
                Object raw = e.payload.get("status");
                if (raw != null) {
                    TaskStatus s = TaskStatus.fromWire(String.valueOf(raw));
                    if (s != null) {
                        phaseLabel = Labels.statusLabelRes(s);
                        if (s == TaskStatus.AWAITING_APPROVAL && !approvalFired) {
                            approvalFired = true;
                            approvalRequired.fire(taskId);
                        } else if (s.isTerminal() && !terminalFired) {
                            terminalFired = true;
                            terminalReached.fire(s.wire());
                        }
                    }
                }
            } else if (RunEvent.Kind.ERROR == e.kindEnum()) {
                phaseLabel = R.string.phase_failed;
            }
        }
        reasoningCount.setValue(reasoning);
        phaseRes.setValue(phaseLabel);
    }

    private void onStreamFailure(Throwable t) {
        connected.setValue(false);
        if (terminalFired) {
            return; // Already finished; nothing more to reconnect for.
        }
        // Resume from the last cached seq with a small backoff.
        if (reconnectAttempts < 3) {
            reconnectAttempts++;
            final int attempt = reconnectAttempts;
            disposable.add(
                    tasks.cachedMaxSeq(taskId)
                            .delay(2L * attempt, TimeUnit.SECONDS)
                            .flatMapPublisher(newSeq -> tasks.stream(taskId, newSeq))
                            .observeOn(AndroidSchedulers.mainThread())
                            .subscribe(
                                    this::onEvent,
                                    ignored -> { },
                                    this::onStreamClosed));
        }
    }

    private void onStreamClosed() {
        connected.setValue(false);
        if (terminalFired) {
            return;
        }
        // A clean close before a terminal event usually means the server
        // finished; re-verify the task state once.
        disposable.add(
                tasks.detail(taskId)
                        .observeOn(AndroidSchedulers.mainThread())
                        .subscribe(
                                detail -> {
                                    TaskStatus s = detail.statusEnum();
                                    phaseRes.setValue(Labels.statusLabelRes(s));
                                    if (s != null && s.isTerminal() && !terminalFired) {
                                        terminalFired = true;
                                        terminalReached.fire(s.wire());
                                    }
                                },
                                t -> { }));
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
            return (T) new RunViewModel(tasks);
        }
    }
}