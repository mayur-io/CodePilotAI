package dev.codepilotai.data.db.dao;

import androidx.room.Dao;
import androidx.room.Insert;
import androidx.room.OnConflictStrategy;
import androidx.room.Query;
import dev.codepilotai.data.db.entity.EventEntity;
import io.reactivex.rxjava3.core.Flowable;
import io.reactivex.rxjava3.core.Single;
import java.util.List;

@Dao
public interface EventDao {

    @Query("SELECT * FROM run_events WHERE taskId = :taskId ORDER BY seq")
    Flowable<List<EventEntity>> observe(long taskId);

    @Query("SELECT COALESCE(MAX(seq), 0) FROM run_events WHERE taskId = :taskId")
    Single<Long> maxSeq(long taskId);

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    void upsertAll(List<EventEntity> events);

    @Query("DELETE FROM run_events WHERE taskId = :taskId AND seq <= :seq")
    void deleteUpTo(long taskId, long seq);
}