package dev.codepilotai.domain.model;

/** Kind of file change, matching backend {@code ProposedChange.ChangeType}. */
public enum ChangeType {
    CREATE("create"),
    MODIFY("modify"),
    DELETE("delete"),
    RENAME("rename");

    private final String wire;

    ChangeType(String wire) {
        this.wire = wire;
    }

    public String wire() {
        return wire;
    }

    public static ChangeType fromWire(String value) {
        if (value == null) {
            return null;
        }
        for (ChangeType t : values()) {
            if (t.wire.equals(value)) {
                return t;
            }
        }
        return null;
    }
}