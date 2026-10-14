package dev.codepilotai.data.api.dto;

import com.squareup.moshi.Json;

/** Result of {@code POST /repos/sync/}. */
public final class SyncResultDto {

    @Json(name = "detail")
    public String detail;
    @Json(name = "count")
    public int count;
    @Json(name = "synced")
    public boolean synced;

    public SyncResultDto() {}
}