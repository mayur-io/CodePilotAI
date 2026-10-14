package dev.codepilotai.domain.model;

/** One parsed line of a unified diff, ready for rendering. */
public final class DiffLine {

    public enum Kind {
        CONTEXT,
        ADDED,
        REMOVED,
        HUNK_HEADER,
        FILE_HEADER
    }

    public final Kind kind;
    public final String text;

    public DiffLine(Kind kind, String text) {
        this.kind = kind;
        this.text = text;
    }
}