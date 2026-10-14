package dev.codepilotai.data.repo;

import dev.codepilotai.data.api.ApiException;
import dev.codepilotai.data.api.ReposApi;
import dev.codepilotai.data.api.dto.CreateRepositoryRequest;
import dev.codepilotai.data.api.dto.RepositoryDto;
import dev.codepilotai.data.db.AppDatabase;
import dev.codepilotai.data.db.dao.RepoDao;
import dev.codepilotai.data.db.entity.RepositoryEntity;
import dev.codepilotai.domain.model.Repository;
import io.reactivex.rxjava3.core.Completable;
import io.reactivex.rxjava3.core.Flowable;
import io.reactivex.rxjava3.core.Single;
import io.reactivex.rxjava3.schedulers.Schedulers;
import java.util.ArrayList;
import java.util.List;

/** Repository list: live from the network, cached in Room for offline display. */
public final class ReposRepository {

    private final ReposApi api;
    private final AppDatabase db;
    private final RepoDao repoDao;

    public ReposRepository(ReposApi api, AppDatabase db) {
        this.api = api;
        this.db = db;
        this.repoDao = db.repoDao();
    }

    /** UI source: Room cache, updated whenever {@link #refresh()} lands. */
    public Flowable<List<RepositoryEntity>> observeRepos() {
        return repoDao.observeAll().subscribeOn(Schedulers.io());
    }

    /** Pull the first page from the server and replace the cache. */
    public Completable refresh() {
        return api.list()
                .flatMapCompletable(page -> {
                    List<RepositoryEntity> entities = new ArrayList<>();
                    if (page.results != null) {
                        for (RepositoryDto dto : page.results) {
                            entities.add(toEntity(dto));
                        }
                    }
                    return Completable.fromAction(() -> {
                        repoDao.clear();
                        repoDao.upsertAll(entities);
                    });
                })
                .onErrorResumeNext(t -> Completable.error(ApiException.from(t)))
                .subscribeOn(Schedulers.io());
    }

    /** Import from GitHub ({@code POST /repos/sync/}) then refresh the cache. */
    public Completable syncFromGithub() {
        return api.sync()
                .flatMapCompletable(ignored -> refresh())
                .onErrorResumeNext(t -> Completable.error(ApiException.from(t)))
                .subscribeOn(Schedulers.io());
    }

    public Single<Repository> create(String name, String description, boolean isPrivate) {
        return api.create(new CreateRepositoryRequest(name, description, isPrivate, true))
                .doOnSuccess(dto -> repoDao.upsert(toEntity(dto)))
                .map(RepositoryDto::toDomain)
                .onErrorResumeNext(t -> Single.error(ApiException.from(t)))
                .subscribeOn(Schedulers.io());
    }

    public void select(long id) {
        repoDao.clearSelection();
        repoDao.select(id);
    }

    public static RepositoryEntity toEntity(RepositoryDto dto) {
        RepositoryEntity e = new RepositoryEntity();
        e.id = dto.id;
        e.fullName = dto.fullName;
        e.name = dto.name;
        e.description = dto.description;
        e.defaultBranch = dto.defaultBranch;
        e.primaryLanguage = dto.primaryLanguage;
        e.isPrivate = dto.isPrivate;
        e.isArchived = dto.isArchived;
        e.taskCount = dto.taskCount;
        e.createdAt = dto.createdAt;
        return e;
    }
}