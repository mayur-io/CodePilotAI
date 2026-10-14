package dev.codepilotai.data.api.dto;

import com.squareup.moshi.Json;

/** The JWT pair plus profile, returned by the handoff exchange. */
public final class TokenPairDto {

    @Json(name = "access")
    public String access;
    @Json(name = "refresh")
    public String refresh;
    @Json(name = "user")
    public UserDto user;

    public TokenPairDto() {}
}