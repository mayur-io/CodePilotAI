package dev.codepilotai.domain.model;

import java.util.Map;

/** One entry in a task's event stream (SSE frame or replayed history). */
public final class RunEvent {

    public final long seq;
    public final String kind;
    public final Map<String, Object> payload;
    public final String createdAt;

    public RunEvent(long seq, String kind, Map<String, Object> payload, String createdAt) {
        this.seq = seq;
        this.kind = kind;
        this.payload = payload;
        this.createdAt = createdAt;
    }

    public Kind kindEnum() {
        return Kind.fromWire(kind);
    }

    public enum Kind {
        STATUS("status"),
        PHASE("phase"),
        LOG("log"),
        REASONING("reasoning"),
        CHANGE_PROPOSED("change_proposed"),
        CHANGE_DECIDED("change_decided"),
        VERIFICATION("verification"),
        PUSHED("pushed"),
        MESSAGE("message"),
        ERROR("error"),
        DONE("done");

        private final String wire;

        Kind(String wire) {
            this.wire = wire;
        }

        public String wire() {
            return wire;
        }

        public static Kind fromWire(String value) {
            if (value == null) {
                return null;
            }
            for (Kind k : values()) {
                if (k.wire.equals(value)) {
                    return k;
                }
            }
            return null;
        }
    }
}