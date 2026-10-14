package dev.codepilotai.data.api.dto;

import com.squareup.moshi.Json;
import dev.codepilotai.domain.model.ProposedChange;

/** Wire shape of the backend {@code ProposedChangeSerializer}. */
public final class ChangeDto {

    @Json(name = "id")
    public long id;
    @Json(name = "task")
    public Long task;
    @Json(name = "order_index")
    public int orderIndex;
    @Json(name = "path")
    public String path;
    @Json(name = "previous_path")
    public String previousPath;
    @Json(name = "change_type")
    public String changeType;
    @Json(name = "patch")
    public String patch;
    @Json(name = "rationale")
    public String rationale;
    @Json(name = "additions")
    public int additions;
    @Json(name = "deletions")
    public int deletions;
    @Json(name = "status")
    public String status;
    @Json(name = "user_feedback")
    public String userFeedback;
    @Json(name = "error_message")
    public String errorMessage;
    @Json(name = "is_decided")
    public boolean decided;
    @Json(name = "decided_at")
    public String decidedAt;
    @Json(name = "created_at")
    public String createdAt;

    public ChangeDto() {}

    public ProposedChange toDomain() {
        return new ProposedChange(
                id,
                task == null ? 0 : task,
                orderIndex,
                path,
                previousPath,
                changeType,
                patch,
                rationale,
                additions,
                deletions,
                status,
                userFeedback,
                errorMessage,
                decided,
                decidedAt,
                createdAt);
    }
}