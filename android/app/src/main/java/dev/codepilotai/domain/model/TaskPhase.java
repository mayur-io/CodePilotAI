package dev.codepilotai.domain.model;

/** Which stage of the pipeline a task is in, matching backend {@code AgentTask.Phase}. */
public enum TaskPhase {
    QUEUED("queued"),
    PLAN("plan"),
    CLARIFY("clarify"),
    EXPLORE("explore"),
    PROPOSE("propose"),
    AWAIT_APPROVAL("await_approval"),
    APPLY("apply"),
    VERIFY("verify"),
    REPAIR("repair"),
    COMMIT("commit"),
    PUSH("push"),
    DONE("done");

    private final String wire;

    TaskPhase(String wire) {
        this.wire = wire;
    }

    public String wire() {
        return wire;
    }

    public static TaskPhase fromWire(String value) {
        if (value == null) {
            return null;
        }
        for (TaskPhase p : values()) {
            if (p.wire.equals(value)) {
                return p;
            }
        }
        return null;
    }

    @Override
    public String toString() {
        return wire;
    }
}