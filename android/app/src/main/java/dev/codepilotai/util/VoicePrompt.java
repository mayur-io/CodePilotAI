package dev.codepilotai.util;

/** Pure text assembly for the dictation button on the new-task screen. */
public final class VoicePrompt {

    private VoicePrompt() {
    }

    /**
     * Append freshly dictated text to whatever is already typed, separating the
     * two with a single space and trimming each side. Either side being blank is
     * ignored, so dictating into an empty field never leaves a leading space.
     */
    public static String append(String existing, String spoken) {
        String left = existing == null ? "" : existing.trim();
        String right = spoken == null ? "" : spoken.trim();
        if (right.isEmpty()) {
            return left;
        }
        if (left.isEmpty()) {
            return right;
        }
        return left + " " + right;
    }
}
