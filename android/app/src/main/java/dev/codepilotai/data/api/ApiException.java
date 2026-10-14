package dev.codepilotai.data.api;

import java.io.IOException;
import okhttp3.MediaType;
import okhttp3.ResponseBody;
import retrofit2.HttpException;

/** A backend API failure with its structured error code and message. */
public final class ApiException extends Exception {

    public final int httpCode;
    public final String code;

    public ApiException(int httpCode, String code, String message) {
        super(message == null || message.trim().isEmpty() ? "Request failed (" + httpCode + ")." : message);
        this.httpCode = httpCode;
        this.code = code;
    }

    public String userMessage() {
        if (code != null && !code.trim().isEmpty()) {
            return getMessage() + " (" + code + ")";
        }
        return getMessage();
    }

    /** Wrap any throwable into a stable exception the UI can display. */
    public static ApiException from(Throwable t) {
        if (t instanceof ApiException) {
            return (ApiException) t;
        }
        if (t instanceof HttpException) {
            HttpException he = (HttpException) t;
            return new ApiException(he.code(), "http_" + he.code(), decodeBody(he));
        }
        if (t instanceof IOException) {
            return new ApiException(0, "network_error", "Cannot reach the server. Check your connection.");
        }
        return new ApiException(0, "unknown", t.getMessage() == null ? "Unknown error" : t.getMessage());
    }

    /** Try to pull the structured {@code {error:{code,message}}} body off an HTTP error. */
    private static String decodeBody(HttpException he) {
        try {
            ResponseBody body = he.response() == null ? null : he.response().errorBody();
            if (body == null) {
                return he.message();
            }
            MediaType type = body.contentType();
            String text = body.string();
            if (type != null && type.subtype().contains("json") && text.contains("\"error\"")) {
                com.squareup.moshi.Moshi moshi = new com.squareup.moshi.Moshi.Builder().build();
                dev.codepilotai.data.api.dto.ErrorEnvelopeDto envelope =
                        moshi.adapter(dev.codepilotai.data.api.dto.ErrorEnvelopeDto.class).fromJson(text);
                if (envelope != null && envelope.error != null && envelope.error.message != null) {
                    return envelope.error.message;
                }
            }
            return text.trim().isEmpty() ? he.message() : text;
        } catch (Exception ignored) {
            return he.message();
        }
    }
}