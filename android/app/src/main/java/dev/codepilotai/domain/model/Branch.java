package dev.codepilotai.domain.model;

/** A repository branch with its pull-request coordinates. */
public final class Branch {

    public final long id;
    public final String name;
    public final String headSha;
    public final String kind;
    public final boolean isOpen;
    public final Integer prNumber;
    public final String prUrl;
    public final String prState;
    public final boolean isMerged;
    public final String createdAt;

    public Branch(
            long id,
            String name,
            String headSha,
            String kind,
            boolean isOpen,
            Integer prNumber,
            String prUrl,
            String prState,
            boolean isMerged,
            String createdAt) {
        this.id = id;
        this.name = name;
        this.headSha = headSha;
        this.kind = kind;
        this.isOpen = isOpen;
        this.prNumber = prNumber;
        this.prUrl = prUrl;
        this.prState = prState;
        this.isMerged = isMerged;
        this.createdAt = createdAt;
    }
}