package dev.codepilotai.data.db;

import android.content.Context;
import androidx.room.Database;
import androidx.room.Room;
import androidx.room.RoomDatabase;
import dev.codepilotai.data.db.dao.ChangeDao;
import dev.codepilotai.data.db.dao.DraftDao;
import dev.codepilotai.data.db.dao.EventDao;
import dev.codepilotai.data.db.dao.RepoDao;
import dev.codepilotai.data.db.dao.TaskDao;
import dev.codepilotai.data.db.entity.ChangeEntity;
import dev.codepilotai.data.db.entity.EventEntity;
import dev.codepilotai.data.db.entity.RepositoryEntity;
import dev.codepilotai.data.db.entity.TaskDraftEntity;
import dev.codepilotai.data.db.entity.TaskEntity;

/** Room database: offline cache + the task-draft queue. */
@Database(
        entities = {
            RepositoryEntity.class,
            TaskEntity.class,
            EventEntity.class,
            ChangeEntity.class,
            TaskDraftEntity.class,
        },
        version = 1,
        exportSchema = false)
public abstract class AppDatabase extends RoomDatabase {

    public abstract RepoDao repoDao();

    public abstract TaskDao taskDao();

    public abstract EventDao eventDao();

    public abstract ChangeDao changeDao();

    public abstract DraftDao draftDao();

    private static volatile AppDatabase instance;

    public static AppDatabase get(Context context) {
        AppDatabase local = instance;
        if (local == null) {
            synchronized (AppDatabase.class) {
                local = instance;
                if (local == null) {
                    local =
                            Room.databaseBuilder(
                                            context.getApplicationContext(),
                                            AppDatabase.class,
                                            "codepilotai.db")
                                    .build();
                    instance = local;
                }
            }
        }
        return local;
    }
}