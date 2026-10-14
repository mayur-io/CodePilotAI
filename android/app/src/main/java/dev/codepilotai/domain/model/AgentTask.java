package dev.codepilotai.domain.model;

/** The task row as the list/history screens need it. */
public class AgentTask {

    public final long id;
    public final String title;
    public final String prompt;
    public final String status;
    public final String phase;
    public final long repositoryId;
    public final String repositoryFullName;
    public final String baseBranch;
    public final String featureBranch;
    public final boolean awaitingDecision;
    public final boolean isTerminal;
    public final long totalTokens;
    public final String costUsd;
    public final String prUrl;
    public final String createdAt;
    public final String updatedAt;

    public AgentTask(
            long id,
            String title,
            String prompt,
            String status,
            String phase,
            long repositoryId,
            String repositoryFullName,
            String baseBranch,
            String featureBranch,
            boolean awaitingDecision,
            boolean isTerminal,
            long totalTokens,
            String costUsd,
            String prUrl,
            String createdAt,
            String updatedAt) {
        this.id = id;
        this.title = title;
        this.prompt = prompt;
        this.status = status;
        this.phase = phase;
        this.repositoryId = repositoryId;
        this.repositoryFullName = repositoryFullName;
        this.baseBranch = baseBranch;
        this.featureBranch = featureBranch;
        this.awaitingDecision = awaitingDecision;
        this.isTerminal = isTerminal;
        this.totalTokens = totalTokens;
        this.costUsd = costUsd;
        this.prUrl = prUrl;
        this.createdAt = createdAt;
        this.updatedAt = updatedAt;
    }

    public TaskStatus statusEnum() {
        return TaskStatus.fromWire(status);
    }

    public TaskPhase phaseEnum() {
        return TaskPhase.fromWire(phase);
    }
}