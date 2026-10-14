package dev.codepilotai.data.db.entity;

import androidx.room.Entity;
import androidx.room.PrimaryKey;

/**
 * A task draft that has not reached the server yet. Persisted in Room so the
 * input survives an app kill, and picked up by {@link dev.codepilotai.work.DraftSyncWorker}
 * whenever the network returns.
 */
@Entity(tableName = "task_drafts")
public final class TaskDraftEntity {

    @PrimaryKey(autoGenerate = true)
    public long id;
    public long repositoryId;
    public String repositoryFullName;
    public String prompt;
    public String baseBranch;
    public String title;
    public long createdAt;
    public boolean pending;

    public TaskDraftEntity() {}
}