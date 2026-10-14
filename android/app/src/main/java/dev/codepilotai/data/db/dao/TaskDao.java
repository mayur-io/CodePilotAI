package dev.codepilotai.data.db.dao;

import androidx.room.Dao;
import androidx.room.Insert;
import androidx.room.OnConflictStrategy;
import androidx.room.Query;
import dev.codepilotai.data.db.entity.TaskEntity;
import io.reactivex.rxjava3.core.Flowable;
import io.reactivex.rxjava3.core.Single;
import java.util.List;

@Dao
public interface TaskDao {

    @Query("SELECT * FROM tasks ORDER BY createdAt DESC")
    Flowable<List<TaskEntity>> observeAll();

    @Query("SELECT * FROM tasks WHERE id = :id LIMIT 1")
    Single<TaskEntity> byId(long id);

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    void upsert(TaskEntity task);

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    void upsertAll(List<TaskEntity> tasks);
}