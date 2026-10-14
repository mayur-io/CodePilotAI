package dev.codepilotai.domain.model;

import java.util.Collections;
import java.util.List;

/** Result of the verification stage, mirroring the backend's stored JSON. */
public final class VerificationResult {

    public final boolean passed;
    public final boolean skipped;
    public final List<Outcome> outcomes;

    public VerificationResult(boolean passed, boolean skipped, List<Outcome> outcomes) {
        this.passed = passed;
        this.skipped = skipped;
        this.outcomes = outcomes == null ? Collections.emptyList() : outcomes;
    }

    /** Human label used by the result screen. */
    public String statusLabel() {
        if (skipped) {
            return "skipped";
        }
        return passed ? "passed" : "failed";
    }

    /** One formatter/linter/test run. */
    public static final class Outcome {
        public final String kind;
        public final String command;
        public final boolean passed;
        public final int returnCode;
        public final double durationSeconds;
        public final String output;

        public Outcome(
                String kind,
                String command,
                boolean passed,
                int returnCode,
                double durationSeconds,
                String output) {
            this.kind = kind;
            this.command = command;
            this.passed = passed;
            this.returnCode = returnCode;
            this.durationSeconds = durationSeconds;
            this.output = output;
        }
    }
}