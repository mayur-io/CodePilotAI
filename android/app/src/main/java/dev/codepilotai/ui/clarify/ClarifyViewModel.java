package dev.codepilotai.ui.clarify;

import androidx.lifecycle.LiveData;
import androidx.lifecycle.MutableLiveData;
import androidx.lifecycle.ViewModel;
import androidx.lifecycle.ViewModelProvider;
import dev.codepilotai.data.api.dto.ClarificationAnswersRequest;
import dev.codepilotai.data.repo.TaskRepository;
import dev.codepilotai.domain.model.Clarification;
import dev.codepilotai.util.Event;
import io.reactivex.rxjava3.android.schedulers.AndroidSchedulers;
import io.reactivex.rxjava3.disposables.CompositeDisposable;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Clarify screen: the agent's questions before it writes any code.
 *
 * <p>Holds the answers in progress per question key and submits the whole batch
 * at once, because the backend only resumes the run when every question in the
 * round has an answer. A question is complete when it has either a chosen option
 * or a non-blank custom answer.
 */
public final class ClarifyViewModel extends ViewModel {

    private final TaskRepository tasks;
    private final CompositeDisposable disposable = new CompositeDisposable();

    private final MutableLiveData<List<Clarification>> questions =
            new MutableLiveData<>(new ArrayList<>());
    private final MutableLiveData<Boolean> loading = new MutableLiveData<>(false);
    private final MutableLiveData<Boolean> busy = new MutableLiveData<>(false);
    private final MutableLiveData<Boolean> allAnswered = new MutableLiveData<>(false);
    private final Event<Long> submitted = new Event<>();
    private final Event<String> error = new Event<>();

    /** Answer in progress, keyed by question key (last write wins). */
    private final Map<String, String> draftAnswers = new LinkedHashMap<>();

    private long taskId;

    public ClarifyViewModel(TaskRepository tasks) {
        this.tasks = tasks;
    }

    public void start(long taskId) {
        this.taskId = taskId;
        refresh();
    }

    /** Load (or reload) the outstanding round of questions. */
    public void refresh() {
        loading.setValue(true);
        disposable.add(
                tasks.questions(taskId)
                        .observeOn(AndroidSchedulers.mainThread())
                        .subscribe(
                                list -> {
                                    loading.setValue(false);
                                    publish(list);
                                },
                                t -> {
                                    loading.setValue(false);
                                    error.fire(t.getMessage());
                                }));
    }

    private void publish(List<Clarification> list) {
        // Keep only the newest round: older rounds are already answered and the
        // backend returns newest first, so the first round number wins.
        List<Clarification> latest = new ArrayList<>();
        int newestRound = -1;
        for (Clarification c : list) {
            if (newestRound == -1) {
                newestRound = c.round;
            }
            if (c.round == newestRound) {
                latest.add(c);
            }
        }
        questions.setValue(latest);
        recomputeAllAnswered(latest);
    }

    /** Record a chosen option (or custom text) for one question. */
    public void setAnswer(Clarification question, String value) {
        if (value == null || value.trim().isEmpty()) {
            draftAnswers.remove(question.key);
        } else {
            draftAnswers.put(question.key, value.trim());
        }
        List<Clarification> current = questions.getValue();
        recomputeAllAnswered(current == null ? new ArrayList<>() : current);
    }

    /** Current answer text for a question, or {@code null} if none yet. */
    public String answerFor(Clarification question) {
        String draft = draftAnswers.get(question.key);
        if (draft != null) {
            return draft;
        }
        return question.isAnswered() ? question.answer : null;
    }

    private void recomputeAllAnswered(List<Clarification> list) {
        if (list.isEmpty()) {
            allAnswered.setValue(false);
            return;
        }
        for (Clarification c : list) {
            String value = draftAnswers.get(c.key);
            boolean answered = (value != null && !value.trim().isEmpty()) || c.isAnswered();
            if (!answered) {
                allAnswered.setValue(false);
                return;
            }
        }
        allAnswered.setValue(true);
    }

    /** Submit every answer as one batch and let the backend resume the run. */
    public void submit() {
        List<Clarification> current = questions.getValue();
        if (current == null || current.isEmpty()) {
            return;
        }
        List<ClarificationAnswersRequest.Item> items = new ArrayList<>();
        for (Clarification c : current) {
            String answer = answerFor(c);
            if (answer == null || answer.trim().isEmpty()) {
                error.fire("Please answer every question.");
                return;
            }
            items.add(new ClarificationAnswersRequest.Item(c.id, answer));
        }

        busy.setValue(true);
        disposable.add(
                tasks.answerQuestions(taskId, items)
                        .observeOn(AndroidSchedulers.mainThread())
                        .subscribe(
                                updated -> {
                                    busy.setValue(false);
                                    submitted.fire(taskId);
                                },
                                t -> {
                                    busy.setValue(false);
                                    error.fire(t.getMessage());
                                }));
    }

    public LiveData<List<Clarification>> questions() {
        return questions;
    }

    public LiveData<Boolean> loading() {
        return loading;
    }

    public LiveData<Boolean> busy() {
        return busy;
    }

    public LiveData<Boolean> allAnswered() {
        return allAnswered;
    }

    public LiveData<Long> submitted() {
        return submitted.asLiveData();
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
            return (T) new ClarifyViewModel(tasks);
        }
    }
}
