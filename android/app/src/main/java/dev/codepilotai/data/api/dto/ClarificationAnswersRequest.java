package dev.codepilotai.data.api.dto;

import com.squareup.moshi.Json;
import java.util.ArrayList;
import java.util.List;

/**
 * Request body for {@code POST /tasks/{id}/questions/answer/}
 * ({@code ClarificationAnswersSerializer}).
 *
 * <p>Answers are submitted as one batch: the backend only resumes the run when
 * every question in the outstanding round is answered, so the agent always sees
 * a complete set of decisions rather than a half-filled one.
 */
public final class ClarificationAnswersRequest {

    @Json(name = "answers")
    public final List<Item> answers;

    public ClarificationAnswersRequest(List<Item> answers) {
        this.answers = answers == null ? new ArrayList<>() : answers;
    }

    /** One question id paired with the user's answer. */
    public static final class Item {
        @Json(name = "id")
        public final long id;
        @Json(name = "answer")
        public final String answer;

        public Item(long id, String answer) {
            this.id = id;
            this.answer = answer == null ? "" : answer;
        }
    }
}
