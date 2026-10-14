package dev.codepilotai.domain.model;

/** Approval state of one proposed change. */
public enum ChangeStatus {
    PENDING("pending"),
    APPROVED("approved"),
    REJECTED("rejected"),
    APPLIED("applied"),
    FAILED("failed"),
    SKIPPED("skipped");

    private final String wire;

    ChangeStatus(String wire) {
        this.wire = wire;
    }

    public String wire() {
        return wire;
    }

    public static ChangeStatus fromWire(String value) {
        if (value == null) {
            return null;
        }
        for (ChangeStatus s : values()) {
            if (s.wire.equals(value)) {
                return s;
            }
        }
        return null;
    }

    public boolean isDecided() {
        return this != PENDING;
    }
}