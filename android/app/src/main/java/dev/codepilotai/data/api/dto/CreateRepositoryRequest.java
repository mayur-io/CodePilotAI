package dev.codepilotai.data.api.dto;

import com.squareup.moshi.Json;

/** Request body for creating a repository ({@code RepositoryCreateSerializer}). */
public final class CreateRepositoryRequest {

    @Json(name = "name")
    public final String name;
    @Json(name = "description")
    public final String description;
    @Json(name = "private")
    public final boolean isPrivate;
    @Json(name = "auto_init")
    public final boolean autoInit;

    public CreateRepositoryRequest(String name, String description, boolean isPrivate, boolean autoInit) {
        this.name = name;
        this.description = description == null ? "" : description;
        this.isPrivate = isPrivate;
        this.autoInit = autoInit;
    }
}