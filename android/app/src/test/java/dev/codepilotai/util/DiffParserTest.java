package dev.codepilotai.util;

import static com.google.common.truth.Truth.assertThat;

import dev.codepilotai.domain.model.DiffLine;
import java.util.List;
import org.junit.Test;

/** Pure-Java checks for the unified-diff parser used by the diff viewer. */
public final class DiffParserTest {

    @Test
    public void parse_emptyPatch_returnsEmpty() {
        assertThat(DiffParser.parse(null)).isEmpty();
        assertThat(DiffParser.parse("")).isEmpty();
        assertThat(DiffParser.parse("   ")).isEmpty();
    }

    @Test
    public void parse_classifiesKinds() {
        String patch =
                "--- a/src/Main.java\n"
                        + "+++ b/src/Main.java\n"
                        + "@@ -10,4 +10,5 @@\n"
                        + " public class Main {\n"
                        + "-    int old = 1;\n"
                        + "+    int next = 2;\n"
                        + " }\n";
        List<DiffLine> lines = DiffParser.parse(patch);

        assertThat(lines).hasSize(7);
        assertThat(lines.get(0).kind).isEqualTo(DiffLine.Kind.FILE_HEADER);
        assertThat(lines.get(1).kind).isEqualTo(DiffLine.Kind.FILE_HEADER);
        assertThat(lines.get(2).kind).isEqualTo(DiffLine.Kind.HUNK_HEADER);
        assertThat(lines.get(3).kind).isEqualTo(DiffLine.Kind.CONTEXT);
        assertThat(lines.get(4).kind).isEqualTo(DiffLine.Kind.REMOVED);
        assertThat(lines.get(5).kind).isEqualTo(DiffLine.Kind.ADDED);

        // Prefix markers are stripped from the rendered text.
        assertThat(lines.get(4).text).isEqualTo("    int old = 1;");
        assertThat(lines.get(5).text).isEqualTo("    int next = 2;");
    }

    @Test
    public void parse_noNewlineMarker_isContext() {
        List<DiffLine> lines = DiffParser.parse("--- a/x\n+++ b/x\n\\ No newline at end of file\n");
        assertThat(lines).hasSize(3);
        assertThat(lines.get(2).kind).isEqualTo(DiffLine.Kind.CONTEXT);
    }

    @Test
    public void counts_tracksAddAndDelete() {
        String patch = "diff --git a/a b/a\n@@ -1,2 +1,2 @@\n+a\n-b\n+c\n";
        int[] counts = DiffParser.counts(patch);
        assertThat(counts[0]).isEqualTo(2); // added
        assertThat(counts[1]).isEqualTo(1); // removed
    }
}