package dev.codepilotai.data.api.dto;

import com.squareup.moshi.Json;
import dev.codepilotai.domain.model.Repository;
import java.util.Collections;
import java.util.List;

/** Wire shape of the backend {@code RepositorySerializer}. */
public final class RepositoryDto {

    @Json(name = "id")
    public long id;
    @Json(name = "github_id")
    public long githubId;
    @Json(name = "name")
    public String name;
    @Json(name = "full_name")
    public String fullName;
    @Json(name = "description")
    public String description;
    @Json(name = "html_url")
    public String htmlUrl;
    @Json(name = "default_branch")
    public String defaultBranch;
    @Json(name = "primary_language")
    public String primaryLanguage;
    @Json(name = "topics")
    public List<String> topics;
    @Json(name = "is_private")
    public boolean isPrivate;
    @Json(name = "is_fork")
    public boolean isFork;
    @Json(name = "is_archived")
    public boolean isArchived;
    @Json(name = "size_kb")
    public long sizeKb;
    @Json(name = "is_cloned")
    public boolean isCloned;
    @Json(name = "clone_url")
    public String cloneUrl;
    @Json(name = "needs_sync")
    public boolean needsSync;
    @Json(name = "task_count")
    public int taskCount;
    @Json(name = "last_opened_at")
    public String lastOpenedAt;
    @Json(name = "created_at")
    public String createdAt;

    public RepositoryDto() {}

    public Repository toDomain() {
        return new Repository(
                id,
                githubId,
                name,
                fullName,
                description,
                htmlUrl,
                defaultBranch,
                primaryLanguage,
                topics == null ? Collections.emptyList() : topics,
                isPrivate,
                isFork,
                isArchived,
                sizeKb,
                isCloned,
                cloneUrl,
                needsSync,
                taskCount,
                lastOpenedAt,
                createdAt);
    }
}