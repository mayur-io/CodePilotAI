package dev.codepilotai.data.api.dto;

import com.squareup.moshi.Json;

/** Request body for {@code POST /devices} ({@code DeviceTokenSerializer}). */
public final class DeviceTokenRequest {

    @Json(name = "token")
    public final String token;
    @Json(name = "platform")
    public final String platform;

    public DeviceTokenRequest(String token, String platform) {
        this.token = token;
        this.platform = platform == null ? "android" : platform;
    }
}
