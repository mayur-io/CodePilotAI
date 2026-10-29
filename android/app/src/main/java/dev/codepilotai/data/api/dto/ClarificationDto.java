package dev.codepilotai.data.api.dto;

import com.squareup.moshi.Json;
import dev.codepilotai.domain.model.Clarification;
import java.util.ArrayList;
import java.util.List;

/**
 * Wire shape of {@code ClarificationSerializer}: one question the agent asked
 * before writing code, together with its options and (once given) the answer.
 */
public final class ClarificationDto {

    @Json(name = "id")
    public long id;
    @Json(name = "task")
    public long task;
    @Json(name = "order_index")
    public int orderIndex;
    @Json(name = "round")
    public int round;
    @Json(name = "key")
    public String key;
    @Json(name = "question")
    public String question;
    @Json(name = "kind")
    public String kind;
    @Json(name = "options")
    public List<OptionDto> options;
    @Json(name = "allow_custom")
    public boolean allowCustom;
    @Json(name = "rationale")
    public String rationale;
    @Json(name = "answer")
    public String answer;
    @Json(name = "is_answered")
    public boolean isAnswered;

    public ClarificationDto() {}

    public Clarification toDomain() {
        List<Clarification.Option> mapped = new ArrayList<>();
        if (options != null) {
            for (OptionDto option : options) {
                mapped.add(option.toDomain());
            }
        }
        return new Clarification(
                id,
                round,
                key,
                question,
                kind,
                mapped,
                allowCustom,
                rationale,
                answer);
    }

    /** One choice inside a question. */
    public static final class OptionDto {
        @Json(name = "value")
        public String value;
        @Json(name = "label")
        public String label;

        public Clarification.Option toDomain() {
            return new Clarification.Option(value, label == null ? value : label);
        }
    }
}
