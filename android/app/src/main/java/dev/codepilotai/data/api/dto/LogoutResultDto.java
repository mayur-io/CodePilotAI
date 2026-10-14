package dev.codepilotai.data.api.dto;

import com.squareup.moshi.Json;

/** Result of {@code POST /auth/logout}. */
public final class LogoutResultDto {

    @Json(name = "detail")
    public String detail;
    @Json(name = "revoked")
    public boolean revoked;

    public LogoutResultDto() {}
}