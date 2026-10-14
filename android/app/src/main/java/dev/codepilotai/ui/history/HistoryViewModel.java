package dev.codepilotai.ui.history;

import androidx.lifecycle.LiveData;
import androidx.lifecycle.MutableLiveData;
import androidx.lifecycle.ViewModel;
import androidx.lifecycle.ViewModelProvider;
import dev.codepilotai.data.db.entity.TaskEntity;
import dev.codepilotai.data.repo.TaskRepository;
import dev.codepilotai.util.Event;
import io.reactivex.rxjava3.android.schedulers.AndroidSchedulers;
import io.reactivex.rxjava3.disposables.CompositeDisposable;
import io.reactivex.rxjava3.schedulers.Schedulers;
import java.util.List;

/** History list: cached tasks with a pull-to-refresh. */
public final class HistoryViewModel extends ViewModel {

    private final TaskRepository tasks;
    private final CompositeDisposable disposable = new CompositeDisposable();

    private final MutableLiveData<List<TaskEntity>> taskList = new MutableLiveData<>();
    private final MutableLiveData<Boolean> refreshing = new MutableLiveData<>(false);
    private final Event<Long> opened = new Event<>();
    private final Event<String> error = new Event<>();

    public HistoryViewModel(TaskRepository tasks) {
        this.tasks = tasks;
        disposable.add(
                tasks.observeTasks()
                        .observeOn(AndroidSchedulers.mainThread())
                        .subscribe(taskList::setValue, t -> error.fire(t.getMessage())));
    }

    public LiveData<List<TaskEntity>> taskList() {
        return taskList;
    }

    public LiveData<Boolean> refreshing() {
        return refreshing;
    }

    public LiveData<Long> opened() {
        return opened.asLiveData();
    }

    public LiveData<String> errors() {
        return error.asLiveData();
    }

    public void refresh() {
        refreshing.setValue(true);
        disposable.add(
                tasks.refreshTasks()
                        .observeOn(AndroidSchedulers.mainThread())
                        .subscribe(
                                () -> refreshing.setValue(false),
                                t -> {
                                    refreshing.setValue(false);
                                    error.fire(t.getMessage());
                                }));
    }

    public void open(TaskEntity task) {
        opened.fire(task.id);
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
            return (T) new HistoryViewModel(tasks);
        }
    }
}