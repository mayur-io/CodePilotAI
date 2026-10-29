package dev.codepilotai.domain.model;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * One question the agent asked before writing code, from the clarify phase.
 *
 * <p>This is the senior-engineer move: for an underspecified ask the agent pins
 * down the choices that would change what it builds (language, styling, auth
 * flow) before proposing any diff. {@link Option}s make the answer tappable
 * instead of free text.
 */
public final class Clarification {

    /** Question kinds, matching backend {@code Clarification.Kind}. */
    public static final String KIND_SINGLE = "single_choice";
    public static final String KIND_MULTI = "multi_choice";
    public static final String KIND_TEXT = "text";

    public final long id;
    public final int round;
    public final String key;
    public final String question;
    public final String kind;
    public final List<Option> options;
    public final boolean allowCustom;
    public final String rationale;
    public final String answer;

    public Clarification(
            long id,
            int round,
            String key,
            String question,
            String kind,
            List<Option> options,
            boolean allowCustom,
            String rationale,
            String answer) {
        this.id = id;
        this.round = round;
        this.key = key;
        this.question = question;
        this.kind = kind;
        this.options = options == null
                ? Collections.emptyList() : Collections.unmodifiableList(new ArrayList<>(options));
        this.allowCustom = allowCustom;
        this.rationale = rationale;
        this.answer = answer;
    }

    public boolean isAnswered() {
        return answer != null && !answer.trim().isEmpty();
    }

    public boolean isText() {
        return KIND_TEXT.equals(kind);
    }

    public boolean isMultiChoice() {
        return KIND_MULTI.equals(kind);
    }

    public boolean isChoice() {
        return KIND_SINGLE.equals(kind) || KIND_MULTI.equals(kind);
    }

    /** One selectable answer. */
    public static final class Option {
        public final String value;
        public final String label;

        public Option(String value, String label) {
            this.value = value;
            this.label = label;
        }
    }
}
