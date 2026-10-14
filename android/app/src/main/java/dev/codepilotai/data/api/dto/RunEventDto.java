package dev.codepilotai.data.api.dto;

import com.squareup.moshi.Json;
import dev.codepilotai.domain.model.RunEvent;
import java.util.Map;

/** Wire shape of {@code RunEventSerializer}. */
public final class RunEventDto {

    @Json(name = "seq")
    public long seq;
    @Json(name = "kind")
    public String kind;
    @Json(name = "payload")
    public Map<String, Object> payload;
    @Json(name = "created_at")
    public String createdAt;

    public RunEventDto() {}

    public RunEvent toDomain() {
        return new RunEvent(seq, kind, payload, createdAt);
    }
}