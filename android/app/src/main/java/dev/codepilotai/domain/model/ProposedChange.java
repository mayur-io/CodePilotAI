package dev.codepilotai.domain.model;

/** One file the agent proposes to change, with its unified diff and state. */
public final class ProposedChange {

    public final long id;
    public final long taskId;
    public final int orderIndex;
    public final String path;
    public final String previousPath;
    public final String changeType;
    public final String patch;
    public final String rationale;
    public final int additions;
    public final int deletions;
    public final String status;
    public final String userFeedback;
    public final String errorMessage;
    public final boolean decided;
    public final String decidedAt;
    public final String createdAt;

    public ProposedChange(
            long id,
            long taskId,
            int orderIndex,
            String path,
            String previousPath,
            String changeType,
            String patch,
            String rationale,
            int additions,
            int deletions,
            String status,
            String userFeedback,
            String errorMessage,
            boolean decided,
            String decidedAt,
            String createdAt) {
        this.id = id;
        this.taskId = taskId;
        this.orderIndex = orderIndex;
        this.path = path;
        this.previousPath = previousPath;
        this.changeType = changeType;
        this.patch = patch;
        this.rationale = rationale;
        this.additions = additions;
        this.deletions = deletions;
        this.status = status;
        this.userFeedback = userFeedback;
        this.errorMessage = errorMessage;
        this.decided = decided;
        this.decidedAt = decidedAt;
        this.createdAt = createdAt;
    }

    public ChangeType changeTypeEnum() {
        return ChangeType.fromWire(changeType);
    }

    public ChangeStatus statusEnum() {
        return ChangeStatus.fromWire(status);
    }
}