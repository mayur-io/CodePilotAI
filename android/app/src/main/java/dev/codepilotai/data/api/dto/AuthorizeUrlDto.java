package dev.codepilotai.data.api.dto;

import com.squareup.moshi.Json;

/** Response of {@code GET /auth/github/start}. */
public final class AuthorizeUrlDto {

    @Json(name = "authorize_url")
    public String authorizeUrl;
    @Json(name = "expires_in")
    public long expiresIn;

    public AuthorizeUrlDto() {}
}