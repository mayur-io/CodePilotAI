package dev.codepilotai.data.api.dto;

import com.squareup.moshi.Json;
import dev.codepilotai.domain.model.AgentTask;
import dev.codepilotai.domain.model.AgentTaskDetail;
import dev.codepilotai.domain.model.VerificationResult;
import java.util.ArrayList;
import java.util.List;

/**
 * Wire shape shared by {@code AgentTaskSummarySerializer} and
 * {@code AgentTaskDetailSerializer}. List responses simply omit the detail-only
 * fields, which stay null/zero.
 */
public final class TaskDetailDto {

    @Json(name = "id")
    public long id;
    @Json(name = "title")
    public String title;
    @Json(name = "prompt")
    public String prompt;
    @Json(name = "status")
    public String status;
    @Json(name = "phase")
    public String phase;
    @Json(name = "repository")
    public Long repository;
    @Json(name = "repository_full_name")
    public String repositoryFullName;
    @Json(name = "base_branch")
    public String baseBranch;
    @Json(name = "feature_branch")
    public String featureBranch;
    @Json(name = "awaiting_decision")
    public boolean awaitingDecision;
    @Json(name = "is_terminal")
    public boolean isTerminal;
    @Json(name = "total_tokens")
    public long totalTokens;
    @Json(name = "cached_tokens")
    public long cachedTokens;
    @Json(name = "cost_usd")
    public String costUsd;
    @Json(name = "pr_url")
    public String prUrl;
    @Json(name = "created_at")
    public String createdAt;
    @Json(name = "updated_at")
    public String updatedAt;

    // Detail-only fields.
    @Json(name = "agent_summary")
    public String agentSummary;
    @Json(name = "agent_reasoning")
    public String agentReasoning;
    @Json(name = "detected_stack")
    public Object detectedStack;
    @Json(name = "diff_stat")
    public String diffStat;
    @Json(name = "verification")
    public VerificationDto verification;
    @Json(name = "commit_sha")
    public String commitSha;
    @Json(name = "commit_message")
    public String commitMessage;
    @Json(name = "pr_number")
    public Integer prNumber;
    @Json(name = "input_tokens")
    public long inputTokens;
    @Json(name = "output_tokens")
    public long outputTokens;
    @Json(name = "reasoning_tokens")
    public long reasoningTokens;
    @Json(name = "llm_calls")
    public int llmCalls;
    @Json(name = "tool_calls")
    public int toolCalls;
    @Json(name = "error_code")
    public String errorCode;
    @Json(name = "error_message")
    public String errorMessage;
    @Json(name = "started_at")
    public String startedAt;
    @Json(name = "finished_at")
    public String finishedAt;
    @Json(name = "duration_seconds")
    public Double durationSeconds;

    public TaskDetailDto() {}

    public AgentTask toSummary() {
        return new AgentTask(
                id,
                title,
                prompt,
                status,
                phase,
                repository == null ? 0 : repository,
                repositoryFullName,
                baseBranch,
                featureBranch,
                awaitingDecision,
                isTerminal,
                totalTokens,
                costUsd,
                prUrl,
                createdAt,
                updatedAt);
    }

    public AgentTaskDetail toDetail() {
        return new AgentTaskDetail(
                toSummary(),
                agentSummary,
                agentReasoning,
                detectedStack == null ? null : String.valueOf(detectedStack),
                diffStat,
                verification == null ? null : verification.toDomain(),
                commitSha,
                commitMessage,
                prNumber,
                inputTokens,
                outputTokens,
                reasoningTokens,
                cachedTokens,
                llmCalls,
                toolCalls,
                errorCode,
                errorMessage,
                startedAt,
                finishedAt,
                durationSeconds);
    }

    /** Wire shape of the stored {@code verification} JSON on a task row. */
    public static final class VerificationDto {
        @Json(name = "passed")
        public boolean passed;
        @Json(name = "skipped")
        public boolean skipped;
        @Json(name = "outcomes")
        public List<OutcomeDto> outcomes;

        public VerificationResult toDomain() {
            List<VerificationResult.Outcome> mapped = new ArrayList<>();
            if (outcomes != null) {
                for (OutcomeDto o : outcomes) {
                    mapped.add(o.toDomain());
                }
            }
            return new VerificationResult(passed, skipped, mapped);
        }
    }

    public static final class OutcomeDto {
        @Json(name = "kind")
        public String kind;
        @Json(name = "command")
        public String command;
        @Json(name = "passed")
        public boolean passed;
        @Json(name = "returncode")
        public int returnCode;
        @Json(name = "duration_seconds")
        public double durationSeconds;
        @Json(name = "output")
        public String output;

        public VerificationResult.Outcome toDomain() {
            return new VerificationResult.Outcome(
                    kind, command, passed, returnCode, durationSeconds, output);
        }
    }
}