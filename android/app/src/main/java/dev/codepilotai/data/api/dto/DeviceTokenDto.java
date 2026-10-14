package dev.codepilotai.data.api.dto;

import com.squareup.moshi.Json;

/** Response body for {@code POST /devices} ({@code DeviceTokenSerializer}). */
public final class DeviceTokenDto {

    @Json(name = "id")
    public long id;
    @Json(name = "token")
    public String token;
    @Json(name = "platform")
    public String platform;
    @Json(name = "last_seen_at")
    public String lastSeenAt;
}
