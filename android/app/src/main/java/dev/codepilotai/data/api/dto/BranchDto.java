package dev.codepilotai.data.api.dto;

import com.squareup.moshi.Json;
import dev.codepilotai.domain.model.Branch;

/** Wire shape of the backend {@code BranchSerializer}. */
public final class BranchDto {

    @Json(name = "id")
    public long id;
    @Json(name = "name")
    public String name;
    @Json(name = "head_sha")
    public String headSha;
    @Json(name = "kind")
    public String kind;
    @Json(name = "is_open")
    public boolean isOpen;
    @Json(name = "pr_number")
    public Integer prNumber;
    @Json(name = "pr_url")
    public String prUrl;
    @Json(name = "pr_state")
    public String prState;
    @Json(name = "is_merged")
    public boolean isMerged;
    @Json(name = "created_at")
    public String createdAt;

    public BranchDto() {}

    public Branch toDomain() {
        return new Branch(id, name, headSha, kind, isOpen, prNumber, prUrl, prState, isMerged, createdAt);
    }
}