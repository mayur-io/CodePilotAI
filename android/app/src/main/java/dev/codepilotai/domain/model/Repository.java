package dev.codepilotai.domain.model;

import java.util.List;

/** A repository the user has connected, mirroring the backend RepositorySerializer. */
public final class Repository {

    public final long id;
    public final long githubId;
    public final String name;
    public final String fullName;
    public final String description;
    public final String htmlUrl;
    public final String defaultBranch;
    public final String primaryLanguage;
    public final List<String> topics;
    public final boolean isPrivate;
    public final boolean isFork;
    public final boolean isArchived;
    public final long sizeKb;
    public final boolean isCloned;
    public final String cloneUrl;
    public final boolean needsSync;
    public final int taskCount;
    public final String lastOpenedAt;
    public final String createdAt;

    public Repository(
            long id,
            long githubId,
            String name,
            String fullName,
            String description,
            String htmlUrl,
            String defaultBranch,
            String primaryLanguage,
            List<String> topics,
            boolean isPrivate,
            boolean isFork,
            boolean isArchived,
            long sizeKb,
            boolean isCloned,
            String cloneUrl,
            boolean needsSync,
            int taskCount,
            String lastOpenedAt,
            String createdAt) {
        this.id = id;
        this.githubId = githubId;
        this.name = name;
        this.fullName = fullName;
        this.description = description;
        this.htmlUrl = htmlUrl;
        this.defaultBranch = defaultBranch;
        this.primaryLanguage = primaryLanguage;
        this.topics = topics;
        this.isPrivate = isPrivate;
        this.isFork = isFork;
        this.isArchived = isArchived;
        this.sizeKb = sizeKb;
        this.isCloned = isCloned;
        this.cloneUrl = cloneUrl;
        this.needsSync = needsSync;
        this.taskCount = taskCount;
        this.lastOpenedAt = lastOpenedAt;
        this.createdAt = createdAt;
    }
}