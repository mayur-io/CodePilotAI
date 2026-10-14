package dev.codepilotai.data.db.dao;

import androidx.room.Dao;
import androidx.room.Insert;
import androidx.room.OnConflictStrategy;
import androidx.room.Query;
import dev.codepilotai.data.db.entity.ChangeEntity;
import io.reactivex.rxjava3.core.Flowable;
import java.util.List;

@Dao
public interface ChangeDao {

    @Query("SELECT * FROM proposed_changes WHERE taskId = :taskId ORDER BY orderIndex")
    Flowable<List<ChangeEntity>> observe(long taskId);

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    void upsertAll(List<ChangeEntity> changes);
}