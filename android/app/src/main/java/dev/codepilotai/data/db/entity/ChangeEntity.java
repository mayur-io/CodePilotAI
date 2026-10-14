package dev.codepilotai.data.db.entity;

import androidx.room.Entity;
import androidx.room.PrimaryKey;

/** Cached proposed change (full diff kept for offline review). */
@Entity(tableName = "proposed_changes")
public final class ChangeEntity {

    @PrimaryKey public long id;
    public long taskId;
    public int orderIndex;
    public String path;
    public String changeType;
    public String patch;
    public String rationale;
    public int additions;
    public int deletions;
    public String status;
    public String createdAt;

    public ChangeEntity() {}
}