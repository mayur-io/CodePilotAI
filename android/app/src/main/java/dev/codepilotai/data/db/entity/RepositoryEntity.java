package dev.codepilotai.data.db.entity;

import androidx.room.Entity;
import androidx.room.PrimaryKey;

/** Cached copy of a connected repository for offline display. */
@Entity(tableName = "repos")
public final class RepositoryEntity {

    @PrimaryKey public long id;
    public String fullName;
    public String name;
    public String description;
    public String defaultBranch;
    public String primaryLanguage;
    public boolean isPrivate;
    public boolean isArchived;
    public int taskCount;
    public String createdAt;
    /** True while the user has this repo selected in the new-task form. */
    public boolean selected;

    public RepositoryEntity() {}
}