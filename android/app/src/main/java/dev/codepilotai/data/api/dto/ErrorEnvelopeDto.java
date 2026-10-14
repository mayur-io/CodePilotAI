package dev.codepilotai.data.api.dto;

import com.squareup.moshi.Json;

/** The backend's error envelope: {@code {error: {code, message}}}. */
public final class ErrorEnvelopeDto {

    @Json(name = "error")
    public ErrorBody error;

    public ErrorEnvelopeDto() {}

    public static final class ErrorBody {
        @Json(name = "code")
        public String code;
        @Json(name = "message")
        public String message;
    }
}