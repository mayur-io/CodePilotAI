package dev.codepilotai.util;

import static com.google.common.truth.Truth.assertThat;

import org.junit.Test;

/** Combining dictated text with what the user already typed. */
public final class VoicePromptTest {

    @Test
    public void append_intoEmptyField_returnsSpokenText() {
        assertThat(VoicePrompt.append("", "Add a login screen")).isEqualTo("Add a login screen");
    }

    @Test
    public void append_afterTypedText_separatesWithOneSpace() {
        assertThat(VoicePrompt.append("Refactor the parser", "and add tests"))
                .isEqualTo("Refactor the parser and add tests");
    }

    @Test
    public void append_trimsBothSidesBeforeJoining() {
        assertThat(VoicePrompt.append("  keep this  ", "  and this  "))
                .isEqualTo("keep this and this");
    }

    @Test
    public void append_blankSpoken_keepsExistingUnchanged() {
        assertThat(VoicePrompt.append("already typed", "   ")).isEqualTo("already typed");
    }

    @Test
    public void append_blankExisting_doesNotLeaveLeadingSpace() {
        assertThat(VoicePrompt.append("   ", "spoken")).isEqualTo("spoken");
    }

    @Test
    public void append_handlesNulls() {
        assertThat(VoicePrompt.append(null, "spoken")).isEqualTo("spoken");
        assertThat(VoicePrompt.append("typed", null)).isEqualTo("typed");
        assertThat(VoicePrompt.append(null, null)).isEmpty();
    }
}
