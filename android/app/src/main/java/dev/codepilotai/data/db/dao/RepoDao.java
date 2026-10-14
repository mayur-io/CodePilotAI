package dev.codepilotai.data.db.dao;

import androidx.room.Dao;
import androidx.room.Insert;
import androidx.room.OnConflictStrategy;
import androidx.room.Query;
import dev.codepilotai.data.db.entity.RepositoryEntity;
import io.reactivex.rxjava3.core.Flowable;
import io.reactivex.rxjava3.core.Single;
import java.util.List;

@Dao
public interface RepoDao {

    @Query("SELECT * FROM repos ORDER BY fullName")
    Flowable<List<RepositoryEntity>> observeAll();

    @Query("SELECT * FROM repos WHERE id = :id LIMIT 1")
    Single<RepositoryEntity> byId(long id);

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    void upsertAll(List<RepositoryEntity> repos);

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    void upsert(RepositoryEntity repo);

    @Query("DELETE FROM repos")
    void clear();

    @Query("UPDATE repos SET selected = 0")
    void clearSelection();

    @Query("UPDATE repos SET selected = 1 WHERE id = :id")
    void select(long id);
}