package dev.codepilotai.data.db.entity;

import androidx.room.Entity;
import androidx.room.PrimaryKey;

/** Cached task summary for history and offline access. */
@Entity(tableName = "tasks")
public final class TaskEntity {

    @PrimaryKey public long id;
    public String title;
    public String prompt;
    public String status;
    public String phase;
    public long repositoryId;
    public String repositoryFullName;
    public String featureBranch;
    public String prUrl;
    public String createdAt;
    public String updatedAt;

    public TaskEntity() {}
}