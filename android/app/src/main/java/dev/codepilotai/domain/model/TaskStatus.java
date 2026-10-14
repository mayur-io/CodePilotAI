package dev.codepilotai.domain.model;

import java.util.Locale;

/** Task lifecycle status, matching backend {@code AgentTask.Status}. */
public enum TaskStatus {
    DRAFT("draft"),
    QUEUED("queued"),
    RUNNING("running"),
    AWAITING_APPROVAL("awaiting_approval"),
    APPLYING("applying"),
    VERIFYING("verifying"),
    PUSHED("pushed"),
    MERGED("merged"),
    FAILED("failed"),
    CANCELLED("cancelled");

    private final String wire;

    TaskStatus(String wire) {
        this.wire = wire;
    }

    public String wire() {
        return wire;
    }

    public static TaskStatus fromWire(String value) {
        if (value == null) {
            return null;
        }
        for (TaskStatus s : values()) {
            if (s.wire.equals(value)) {
                return s;
            }
        }
        return null;
    }

    public boolean isTerminal() {
        return this == PUSHED || this == MERGED || this == FAILED || this == CANCELLED;
    }

    @Override
    public String toString() {
        return wire;
    }
}