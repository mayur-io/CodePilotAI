package dev.codepilotai.domain.model;

/** A signed-in user profile as returned by the backend. */
public final class User {

    public final long id;
    public final String username;
    public final String displayName;
    public final String email;
    public final String fullName;
    public final String avatarUrl;
    public final boolean githubConnected;
    public final boolean isStaff;
    public final String dateJoined;

    public User(
            long id,
            String username,
            String displayName,
            String email,
            String fullName,
            String avatarUrl,
            boolean githubConnected,
            boolean isStaff,
            String dateJoined) {
        this.id = id;
        this.username = username;
        this.displayName = displayName;
        this.email = email;
        this.fullName = fullName;
        this.avatarUrl = avatarUrl;
        this.githubConnected = githubConnected;
        this.isStaff = isStaff;
        this.dateJoined = dateJoined;
    }

    /** What the signed-in area of the UI shows. */
    public String label() {
        if (displayName != null && !displayName.trim().isEmpty()) {
            return displayName;
        }
        return username;
    }
}