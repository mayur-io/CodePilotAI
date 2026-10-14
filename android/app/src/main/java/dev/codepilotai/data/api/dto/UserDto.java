package dev.codepilotai.data.api.dto;

import com.squareup.moshi.Json;

/** Wire shape of the backend {@code UserSerializer}. */
public final class UserDto {

    @Json(name = "id")
    public long id;
    @Json(name = "username")
    public String username;
    @Json(name = "display_name")
    public String displayName;
    @Json(name = "email")
    public String email;
    @Json(name = "full_name")
    public String fullName;
    @Json(name = "avatar_url")
    public String avatarUrl;
    @Json(name = "is_github_connected")
    public boolean githubConnected;
    @Json(name = "is_staff")
    public boolean isStaff;
    @Json(name = "date_joined")
    public String dateJoined;

    public UserDto() {}

    public dev.codepilotai.domain.model.User toDomain() {
        return new dev.codepilotai.domain.model.User(
                id,
                username,
                displayName,
                email,
                fullName,
                avatarUrl,
                githubConnected,
                isStaff,
                dateJoined);
    }
}