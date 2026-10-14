package dev.codepilotai.data.api.dto;

import com.squareup.moshi.Json;
import dev.codepilotai.domain.model.ChatMessage;

/** Wire shape of {@code MessageSerializer}. */
public final class MessageDto {

    @Json(name = "id")
    public long id;
    @Json(name = "role")
    public String role;
    @Json(name = "content")
    public String content;
    @Json(name = "created_at")
    public String createdAt;

    public MessageDto() {}

    public ChatMessage toDomain() {
        return new ChatMessage(id, role, content, createdAt);
    }
}