package dev.codepilotai.data.repo;

import com.squareup.moshi.JsonAdapter;
import com.squareup.moshi.Moshi;
import dev.codepilotai.data.api.ApiException;
import dev.codepilotai.data.api.SseClient;
import dev.codepilotai.data.api.TasksApi;
import dev.codepilotai.data.api.dto.ChangeDto;
import dev.codepilotai.data.api.dto.ChangeDecisionRequest;
import dev.codepilotai.data.api.dto.ClarificationAnswersRequest;
import dev.codepilotai.data.api.dto.ClarificationDto;
import dev.codepilotai.data.api.dto.CreateTaskRequest;
import dev.codepilotai.data.api.dto.FollowUpRequest;
import dev.codepilotai.data.api.dto.MessageDto;
import dev.codepilotai.data.api.dto.RunEventDto;
import dev.codepilotai.data.api.dto.TaskDetailDto;
import dev.codepilotai.data.db.AppDatabase;
import dev.codepilotai.data.db.dao.ChangeDao;
import dev.codepilotai.data.db.dao.EventDao;
import dev.codepilotai.data.db.dao.TaskDao;
import dev.codepilotai.data.db.entity.ChangeEntity;
import dev.codepilotai.data.db.entity.EventEntity;
import dev.codepilotai.data.db.entity.TaskEntity;
import dev.codepilotai.domain.model.AgentTask;
import dev.codepilotai.domain.model.AgentTaskDetail;
import dev.codepilotai.domain.model.ChatMessage;
import dev.codepilotai.domain.model.Clarification;
import dev.codepilotai.domain.model.ProposedChange;
import dev.codepilotai.domain.model.RunEvent;
import io.reactivex.rxjava3.core.Completable;
import io.reactivex.rxjava3.core.Flowable;
import io.reactivex.rxjava3.core.Single;
import io.reactivex.rxjava3.schedulers.Schedulers;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/** Task lifecycle: creation, approvals, streaming, and the Room event cache. */
public final class TaskRepository {

    private final TasksApi api;
    private final SseClient sse;
    private final AppDatabase db;
    private final JsonAdapter<RunEventDto> eventAdapter;
    private final TaskDao taskDao;
    private final EventDao eventDao;
    private final ChangeDao changeDao;

    public TaskRepository(TasksApi api, SseClient sse, AppDatabase db, Moshi moshi) {
        this.api = api;
        this.sse = sse;
        this.db = db;
        this.eventAdapter = moshi.adapter(RunEventDto.class);
        this.taskDao = db.taskDao();
        this.eventDao = db.eventDao();
        this.changeDao = db.changeDao();
    }

    // ── tasks ────────────────────────────────────────────────────────────────

    public Single<AgentTaskDetail> create(CreateTaskRequest request) {
        return api.create(request)
                .doOnSuccess(this::cacheTask)
                .map(TaskDetailDto::toDetail)
                .onErrorResumeNext(t -> Single.error(ApiException.from(t)))
                .subscribeOn(Schedulers.io());
    }

    public Flowable<List<TaskEntity>> observeTasks() {
        return taskDao.observeAll().subscribeOn(Schedulers.io());
    }

    /** Refresh the history page (first page only) into Room. */
    public Completable refreshTasks() {
        return api.list()
                .flatMapCompletable(page -> {
                    List<TaskEntity> entities = new ArrayList<>();
                    if (page.results != null) {
                        for (TaskDetailDto dto : page.results) {
                            entities.add(toTaskEntity(dto));
                        }
                    }
                    return Completable.fromAction(() -> taskDao.upsertAll(entities));
                })
                .onErrorResumeNext(t -> Completable.error(ApiException.from(t)))
                .subscribeOn(Schedulers.io());
    }

    public Single<AgentTaskDetail> detail(long id) {
        return api.detail(id)
                .doOnSuccess(this::cacheTask)
                .map(TaskDetailDto::toDetail)
                .onErrorResumeNext(t -> Single.error(ApiException.from(t)))
                .subscribeOn(Schedulers.io());
    }

    public Single<AgentTask> cancel(long id) {
        return api.cancel(id)
                .doOnSuccess(this::cacheTask)
                .map(TaskDetailDto::toSummary)
                .onErrorResumeNext(t -> Single.error(ApiException.from(t)))
                .subscribeOn(Schedulers.io());
    }

    // ── changes & decisions ──────────────────────────────────────────────────

    public Flowable<List<ChangeEntity>> observeChanges(long taskId) {
        return changeDao.observe(taskId).subscribeOn(Schedulers.io());
    }

    public Single<List<ProposedChange>> changes(long taskId) {
        return api.changes(taskId)
                .doOnSuccess(dtos -> {
                    List<ChangeEntity> entities = new ArrayList<>();
                    for (ChangeDto dto : dtos) {
                        entities.add(toChangeEntity(dto));
                    }
                    changeDao.upsertAll(entities);
                })
                .map(dtos -> {
                    List<ProposedChange> out = new ArrayList<>();
                    for (ChangeDto dto : dtos) {
                        out.add(dto.toDomain());
                    }
                    return out;
                })
                .onErrorResumeNext(t -> Single.error(ApiException.from(t)))
                .subscribeOn(Schedulers.io());
    }

    public Single<ProposedChange> decide(
            long taskId, long changeId, String decision, String feedback) {
        return api.decide(taskId, changeId, new ChangeDecisionRequest(decision, feedback))
                .doOnSuccess(dto -> changeDao.upsertAll(Collections.singletonList(toChangeEntity(dto))))
                .map(ChangeDto::toDomain)
                .onErrorResumeNext(t -> Single.error(ApiException.from(t)))
                .subscribeOn(Schedulers.io());
    }

    public Single<AgentTaskDetail> execute(long taskId) {
        return api.execute(taskId)
                .doOnSuccess(this::cacheTask)
                .map(TaskDetailDto::toDetail)
                .onErrorResumeNext(t -> Single.error(ApiException.from(t)))
                .subscribeOn(Schedulers.io());
    }

    // ── messages ─────────────────────────────────────────────────────────────

    public Single<List<ChatMessage>> messages(long taskId) {
        return api.messages(taskId)
                .map(dtos -> {
                    List<ChatMessage> out = new ArrayList<>();
                    for (MessageDto dto : dtos) {
                        out.add(dto.toDomain());
                    }
                    return out;
                })
                .onErrorResumeNext(t -> Single.error(ApiException.from(t)))
                .subscribeOn(Schedulers.io());
    }

    public Single<ChatMessage> postMessage(long taskId, String content) {
        return api.postMessage(taskId, new FollowUpRequest(content))
                .map(MessageDto::toDomain)
                .onErrorResumeNext(t -> Single.error(ApiException.from(t)))
                .subscribeOn(Schedulers.io());
    }

    // ── clarifications ───────────────────────────────────────────────────────

    /** The questions the agent asked, latest round first. */
    public Single<List<Clarification>> questions(long taskId) {
        return api.questions(taskId)
                .map(TaskRepository::toClarifications)
                .onErrorResumeNext(t -> Single.error(ApiException.from(t)))
                .subscribeOn(Schedulers.io());
    }

    /**
     * Submit the answers for the outstanding round and resume the run.
     *
     * <p>The whole round goes in one request: the backend refuses a partial
     * batch, so the agent never runs on an incomplete set of decisions.
     */
    public Single<List<Clarification>> answerQuestions(
            long taskId, List<ClarificationAnswersRequest.Item> answers) {
        return api.answerQuestions(taskId, new ClarificationAnswersRequest(answers))
                .map(TaskRepository::toClarifications)
                .onErrorResumeNext(t -> Single.error(ApiException.from(t)))
                .subscribeOn(Schedulers.io());
    }

    private static List<Clarification> toClarifications(List<ClarificationDto> dtos) {
        List<Clarification> out = new ArrayList<>();
        if (dtos == null) {
            return out;
        }
        for (ClarificationDto dto : dtos) {
            out.add(dto.toDomain());
        }
        return out;
    }

    // ── events & streaming ───────────────────────────────────────────────────

    public Flowable<List<EventEntity>> observeEvents(long taskId) {
        return eventDao.observe(taskId).subscribeOn(Schedulers.io());
    }

    public Single<Long> cachedMaxSeq(long taskId) {
        return eventDao.maxSeq(taskId).subscribeOn(Schedulers.io());
    }

    /** Live SSE stream; the caller re-subscribes with a fresh {@code afterSeq} to resume. */
    public Flowable<RunEvent> stream(long taskId, long afterSeq) {
        return sse.stream(taskId, afterSeq)
                .doOnNext(event -> cacheEvent(taskId, event))
                .subscribeOn(Schedulers.io());
    }

    /** History replay fallback ({@code GET /tasks/{id}/events/}) for reconnect. */
    public Single<List<RunEvent>> eventsAfter(long taskId, long afterSeq) {
        return api.events(taskId, afterSeq)
                .doOnSuccess(dtos -> {
                    List<EventEntity> entities = new ArrayList<>();
                    for (RunEventDto dto : dtos) {
                        entities.add(toEventEntity(taskId, dto.toDomain()));
                    }
                    eventDao.upsertAll(entities);
                })
                .map(dtos -> {
                    List<RunEvent> out = new ArrayList<>();
                    for (RunEventDto dto : dtos) {
                        out.add(dto.toDomain());
                    }
                    return out;
                })
                .onErrorResumeNext(t -> Single.error(ApiException.from(t)))
                .subscribeOn(Schedulers.io());
    }

    /** Map a cached change row back to a domain object for offline approval. */
    public static ProposedChange toDomain(ChangeEntity e) {
        return new ProposedChange(
                e.id,
                e.taskId,
                e.orderIndex,
                e.path,
                null,
                e.changeType,
                e.patch,
                e.rationale,
                e.additions,
                e.deletions,
                e.status,
                null,
                null,
                !"pending".equals(e.status),
                null,
                e.createdAt);
    }

    // ── caching helpers ──────────────────────────────────────────────────────

    /** Cache a task row (used by the app and the offline-draft worker). */
    public void cacheTask(TaskDetailDto dto) {
        taskDao.upsert(toTaskEntity(dto));
    }

    private void cacheEvent(long taskId, RunEvent event) {
        eventDao.upsertAll(Collections.singletonList(toEventEntity(taskId, event)));
    }

    private EventEntity toEventEntity(long taskId, RunEvent event) {
        EventEntity e = new EventEntity();
        e.taskId = taskId;
        e.seq = event.seq;
        e.kind = event.kind;
        e.createdAt = event.createdAt;
        RunEventDto dto = new RunEventDto();
        dto.seq = event.seq;
        dto.kind = event.kind;
        dto.payload = event.payload;
        dto.createdAt = event.createdAt;
        e.payloadJson = eventAdapter.toJson(dto);
        return e;
    }

    /** Reconstruct a {@link RunEvent} from a cached row (used by the run screen). */
    public RunEvent fromEventEntity(EventEntity e) {
        try {
            RunEventDto dto = eventAdapter.fromJson(e.payloadJson);
            if (dto != null) {
                return dto.toDomain();
            }
        } catch (Exception ignored) {
            // fall through to a payload-less event
        }
        return new RunEvent(e.seq, e.kind, Collections.emptyMap(), e.createdAt);
    }

    private static TaskEntity toTaskEntity(TaskDetailDto dto) {
        TaskEntity e = new TaskEntity();
        e.id = dto.id;
        e.title = dto.title;
        e.prompt = dto.prompt;
        e.status = dto.status;
        e.phase = dto.phase;
        e.repositoryId = dto.repository == null ? 0 : dto.repository;
        e.repositoryFullName = dto.repositoryFullName;
        e.featureBranch = dto.featureBranch;
        e.prUrl = dto.prUrl;
        e.createdAt = dto.createdAt;
        e.updatedAt = dto.updatedAt;
        return e;
    }

    private static ChangeEntity toChangeEntity(ChangeDto dto) {
        ChangeEntity e = new ChangeEntity();
        e.id = dto.id;
        e.taskId = dto.task == null ? 0 : dto.task;
        e.orderIndex = dto.orderIndex;
        e.path = dto.path;
        e.changeType = dto.changeType;
        e.patch = dto.patch;
        e.rationale = dto.rationale;
        e.additions = dto.additions;
        e.deletions = dto.deletions;
        e.status = dto.status;
        e.createdAt = dto.createdAt;
        return e;
    }
}