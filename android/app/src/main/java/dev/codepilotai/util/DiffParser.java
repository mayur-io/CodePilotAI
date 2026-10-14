package dev.codepilotai.util;

import dev.codepilotai.domain.model.DiffLine;
import java.util.ArrayList;
import java.util.List;

/**
 * Turns a unified diff into render-ready lines. Pure Java so it is unit-testable
 * on the JVM: file headers, hunk headers, context, additions and deletions each
 * map to a {@link DiffLine.Kind} the adapter colours.
 */
public final class DiffParser {

    private DiffParser() {}

    public static List<DiffLine> parse(String patch) {
        List<DiffLine> lines = new ArrayList<>();
        if (patch == null || patch.trim().isEmpty()) {
            return lines;
        }
        String[] raw = patch.split("\n", -1);
        for (String line : raw) {
            if (line.isEmpty()) {
                continue;
            }
            if (line.startsWith("--- ") || line.startsWith("+++ ")) {
                lines.add(new DiffLine(DiffLine.Kind.FILE_HEADER, line));
            } else if (line.startsWith("@@")) {
                lines.add(new DiffLine(DiffLine.Kind.HUNK_HEADER, line));
            } else if (line.startsWith("+")) {
                lines.add(new DiffLine(DiffLine.Kind.ADDED, line.substring(1)));
            } else if (line.startsWith("-")) {
                lines.add(new DiffLine(DiffLine.Kind.REMOVED, line.substring(1)));
            } else if (line.startsWith("\\")) {
                // "\ No newline at end of file" — keep as neutral context.
                lines.add(new DiffLine(DiffLine.Kind.CONTEXT, line.substring(2)));
            } else {
                lines.add(new DiffLine(DiffLine.Kind.CONTEXT, line.startsWith(" ") ? line.substring(1) : line));
            }
        }
        return lines;
    }

    /** Count added/removed lines for a badge on the approval card. */
    public static int[] counts(String patch) {
        int added = 0;
        int removed = 0;
        for (DiffLine line : parse(patch)) {
            if (line.kind == DiffLine.Kind.ADDED) {
                added++;
            } else if (line.kind == DiffLine.Kind.REMOVED) {
                removed++;
            }
        }
        return new int[] {added, removed};
    }
}