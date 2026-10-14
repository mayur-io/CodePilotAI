package dev.codepilotai.data.api.dto;

import com.squareup.moshi.Json;
import java.util.List;

/** DRF page envelope: {@code {count, next, previous, results}}. */
public final class Page<T> {

    @Json(name = "count")
    public int count;
    @Json(name = "next")
    public String next;
    @Json(name = "previous")
    public String previous;
    @Json(name = "results")
    public List<T> results;

    public Page() {}
}