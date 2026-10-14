package dev.codepilotai.domain.model;

/** One turn in the task's conversational thread. */
public final class ChatMessage {

    public final long id;
    public final String role;
    public final String content;
    public final String createdAt;

    public ChatMessage(long id, String role, String content, String createdAt) {
        this.id = id;
        this.role = role;
        this.content = content;
        this.createdAt = createdAt;
    }
}