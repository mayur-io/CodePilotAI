package dev.codepilotai.data.api.dto;

import com.squareup.moshi.Json;

/** Request body for a follow-up message ({@code MessageCreateSerializer}). */
public final class FollowUpRequest {

    @Json(name = "content")
    public final String content;

    public FollowUpRequest(String content) {
        this.content = content;
    }
}