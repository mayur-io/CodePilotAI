package dev.codepilotai.data.api.dto;

import com.squareup.moshi.Json;

/** Request body for {@code POST /tasks/} ({@code AgentTaskCreateSerializer}). */
public final class CreateTaskRequest {

    @Json(name = "repository_id")
    public final long repositoryId;
    @Json(name = "prompt")
    public final String prompt;
    @Json(name = "base_branch")
    public final String baseBranch;
    @Json(name = "title")
    public final String title;

    public CreateTaskRequest(long repositoryId, String prompt, String baseBranch, String title) {
        this.repositoryId = repositoryId;
        this.prompt = prompt;
        this.baseBranch = baseBranch == null ? "" : baseBranch;
        this.title = title == null ? "" : title;
    }
}