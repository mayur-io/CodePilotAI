package dev.codepilotai.domain.model;

/** A full task detail, adding agent output and verification to {@link AgentTask}. */
public final class AgentTaskDetail extends AgentTask {

    public final String agentSummary;
    public final String agentReasoning;
    public final String detectedStack;
    public final String diffStat;
    public final VerificationResult verification;
    public final String commitSha;
    public final String commitMessage;
    public final Integer prNumber;
    public final long inputTokens;
    public final long outputTokens;
    public final long reasoningTokens;
    public final long cachedTokens;
    public final int llmCalls;
    public final int toolCalls;
    public final String errorCode;
    public final String errorMessage;
    public final String startedAt;
    public final String finishedAt;
    public final Double durationSeconds;

    public AgentTaskDetail(
            AgentTask base,
            String agentSummary,
            String agentReasoning,
            String detectedStack,
            String diffStat,
            VerificationResult verification,
            String commitSha,
            String commitMessage,
            Integer prNumber,
            long inputTokens,
            long outputTokens,
            long reasoningTokens,
            long cachedTokens,
            int llmCalls,
            int toolCalls,
            String errorCode,
            String errorMessage,
            String startedAt,
            String finishedAt,
            Double durationSeconds) {
        super(
                base.id,
                base.title,
                base.prompt,
                base.status,
                base.phase,
                base.repositoryId,
                base.repositoryFullName,
                base.baseBranch,
                base.featureBranch,
                base.awaitingDecision,
                base.isTerminal,
                base.totalTokens,
                base.costUsd,
                base.prUrl,
                base.createdAt,
                base.updatedAt);
        this.agentSummary = agentSummary;
        this.agentReasoning = agentReasoning;
        this.detectedStack = detectedStack;
        this.diffStat = diffStat;
        this.verification = verification;
        this.commitSha = commitSha;
        this.commitMessage = commitMessage;
        this.prNumber = prNumber;
        this.inputTokens = inputTokens;
        this.outputTokens = outputTokens;
        this.reasoningTokens = reasoningTokens;
        this.cachedTokens = cachedTokens;
        this.llmCalls = llmCalls;
        this.toolCalls = toolCalls;
        this.errorCode = errorCode;
        this.errorMessage = errorMessage;
        this.startedAt = startedAt;
        this.finishedAt = finishedAt;
        this.durationSeconds = durationSeconds;
    }

    public long totalTokens() {
        return inputTokens + outputTokens + reasoningTokens;
    }
}