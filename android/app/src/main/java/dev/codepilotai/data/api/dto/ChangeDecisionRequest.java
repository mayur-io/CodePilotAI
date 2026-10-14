package dev.codepilotai.data.api.dto;

import com.squareup.moshi.Json;

/** Request body for deciding on a change ({@code ProposedChangeDecisionSerializer}). */
public final class ChangeDecisionRequest {

    @Json(name = "decision")
    public final String decision;
    @Json(name = "feedback")
    public final String feedback;

    public ChangeDecisionRequest(String decision, String feedback) {
        this.decision = decision;
        this.feedback = feedback == null ? "" : feedback;
    }
}