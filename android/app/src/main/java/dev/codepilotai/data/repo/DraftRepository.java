package dev.codepilotai.data.repo;

import dev.codepilotai.data.api.ApiException;
import dev.codepilotai.data.api.TasksApi;
import dev.codepilotai.data.api.dto.CreateTaskRequest;
import dev.codepilotai.data.db.AppDatabase;
import dev.codepilotai.data.db.dao.DraftDao;
import dev.codepilotai.data.db.entity.TaskDraftEntity;
import io.reactivex.rxjava3.core.Completable;
import io.reactivex.rxjava3.core.Single;
import io.reactivex.rxjava3.schedulers.Schedulers;
import java.util.List;

/** Offline queue: drafts persist in Room; the worker pushes them when online. */
public final class DraftRepository {

    private final TasksApi api;
    private final DraftDao draftDao;
    private final TaskRepository taskRepository;

    public DraftRepository(TasksApi api, AppDatabase db, TaskRepository taskRepository) {
        this.api = api;
        this.draftDao = db.draftDao();
        this.taskRepository = taskRepository;
    }

    /** Save a draft locally; returns {@code true} when it should be sent now. */
    public Single<Long> saveDraft(
            long repositoryId, String repositoryFullName, String prompt, String baseBranch, String title) {
        TaskDraftEntity draft = new TaskDraftEntity();
        draft.repositoryId = repositoryId;
        draft.repositoryFullName = repositoryFullName;
        draft.prompt = prompt;
        draft.baseBranch = baseBranch;
        draft.title = title;
        draft.createdAt = System.currentTimeMillis();
        draft.pending = true;
        return draftDao.insert(draft).subscribeOn(Schedulers.io());
    }

    public Single<List<TaskDraftEntity>> pendingDrafts() {
        return draftDao.pending().subscribeOn(Schedulers.io());
    }

    public Completable clear() {
        return pendingDrafts()
                .flatMapCompletable(
                        list -> Completable.fromAction(
                                () -> {
                                    for (TaskDraftEntity d : list) {
                                        draftDao.delete(d.id);
                                    }
                                }))
                .subscribeOn(Schedulers.io());
    }

    /** Try to push every pending draft. Used by the WorkManager worker so a task
     * composed offline lands the moment connectivity returns. */
    public Single<Integer> syncPending() {
        return pendingDrafts()
                .flatMap(drafts -> io.reactivex.rxjava3.core.Observable.fromIterable(drafts)
                        .concatMapCompletable(this::send)
                        .andThen(io.reactivex.rxjava3.core.Single.just(drafts.size())))
                .onErrorResumeNext(t -> Single.error(ApiException.from(t)))
                .subscribeOn(Schedulers.io());
    }

    private Completable send(TaskDraftEntity draft) {
        return api.create(
                        new CreateTaskRequest(
                                draft.repositoryId,
                                draft.prompt,
                                draft.baseBranch,
                                draft.title))
                .doOnSuccess(taskRepository::cacheTask)
                .flatMapCompletable(ignored -> draftDao.markSynced(draft.id));
    }
}