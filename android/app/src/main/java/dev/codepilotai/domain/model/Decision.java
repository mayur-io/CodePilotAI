package dev.codepilotai.domain.model;

/** Decision the user can make on a proposed change. */
public enum Decision {
    APPROVE("approve"),
    REJECT("reject"),
    REQUEST_EDIT("request_edit");

    private final String wire;

    Decision(String wire) {
        this.wire = wire;
    }

    public String wire() {
        return wire;
    }
}