package dev.codepilotai.domain.model;

import static com.google.common.truth.Truth.assertThat;

import dev.codepilotai.data.api.dto.ClarificationDto;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import org.junit.Test;

/** The clarify-feature domain model and its wire mapping. */
public final class ClarificationTest {

    private static Clarification question(
            long id, String key, String kind, List<Clarification.Option> options, String answer) {
        return new Clarification(
                id, 1, key, "Which " + key + "?", kind, options, true, "Because it changes code.", answer);
    }

    @Test
    public void options_areCopiedDefensively() {
        List<Clarification.Option> source = new ArrayList<>();
        source.add(new Clarification.Option("python", "Python"));
        Clarification c = question(1, "language", Clarification.KIND_SINGLE, source, null);

        source.add(new Clarification.Option("javascript", "JavaScript"));

        assertThat(c.options).hasSize(1);
    }

    @Test
    public void isAnswered_requiresNonBlankText() {
        assertThat(question(1, "language", Clarification.KIND_TEXT, null, null).isAnswered())
                .isFalse();
        assertThat(question(1, "language", Clarification.KIND_TEXT, null, "  ").isAnswered())
                .isFalse();
        assertThat(question(1, "language", Clarification.KIND_TEXT, null, "python").isAnswered())
                .isTrue();
    }

    @Test
    public void kindHelpers_matchTheWireValues() {
        assertThat(question(1, "a", Clarification.KIND_TEXT, null, null).isText()).isTrue();
        assertThat(question(1, "a", Clarification.KIND_TEXT, null, null).isChoice()).isFalse();
        assertThat(question(1, "a", Clarification.KIND_SINGLE, null, null).isChoice()).isTrue();
        assertThat(question(1, "a", Clarification.KIND_MULTI, null, null).isMultiChoice()).isTrue();
    }

    @Test
    public void dto_mapsOptionsAndAnswerToDomain() {
        ClarificationDto dto = new ClarificationDto();
        dto.id = 5;
        dto.round = 2;
        dto.key = "styling";
        dto.question = "How should it look?";
        dto.kind = Clarification.KIND_SINGLE;
        dto.allowCustom = true;
        dto.rationale = "Changes the markup.";
        dto.answer = "";
        ClarificationDto.OptionDto first = new ClarificationDto.OptionDto();
        first.value = "tailwind";
        first.label = "Tailwind CSS";
        ClarificationDto.OptionDto second = new ClarificationDto.OptionDto();
        second.value = "plain";
        second.label = null; // Falls back to the value.
        dto.options = Arrays.asList(first, second);

        Clarification c = dto.toDomain();

        assertThat(c.id).isEqualTo(5L);
        assertThat(c.round).isEqualTo(2);
        assertThat(c.key).isEqualTo("styling");
        assertThat(c.options).hasSize(2);
        assertThat(c.options.get(0).label).isEqualTo("Tailwind CSS");
        assertThat(c.options.get(1).label).isEqualTo("plain");
        assertThat(c.isAnswered()).isFalse();
    }

    @Test
    public void dto_withNullOptions_mapsToEmptyList() {
        ClarificationDto dto = new ClarificationDto();
        dto.options = null;

        assertThat(dto.toDomain().options).isEmpty();
    }
}
