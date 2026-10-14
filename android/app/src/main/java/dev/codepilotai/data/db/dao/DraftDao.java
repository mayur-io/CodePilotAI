package dev.codepilotai.data.db.dao;

import androidx.room.Dao;
import androidx.room.Insert;
import androidx.room.Query;
import dev.codepilotai.data.db.entity.TaskDraftEntity;
import io.reactivex.rxjava3.core.Completable;
import io.reactivex.rxjava3.core.Single;
import java.util.List;

@Dao
public interface DraftDao {

    @Insert
    Single<Long> insert(TaskDraftEntity draft);

    @Query("SELECT * FROM task_drafts WHERE pending = 1 ORDER BY createdAt")
    Single<List<TaskDraftEntity>> pending();

    @Query("UPDATE task_drafts SET pending = 0 WHERE id = :id")
    Completable markSynced(long id);

    @Query("DELETE FROM task_drafts WHERE id = :id")
    Completable delete(long id);
}