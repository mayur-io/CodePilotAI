package dev.codepilotai.domain.model;

import static com.google.common.truth.Truth.assertThat;

import org.junit.Test;

/**
 * Wire-string round trips for the three enums the UI switches on. These are the
 * exact contract with the backend serializer, so a rename here breaks a test.
 */
public final class WireEnumsTest {

    @Test
    public void taskStatus_roundTripsEveryWire() {
        for (TaskStatus s : TaskStatus.values()) {
            assertThat(TaskStatus.fromWire(s.wire())).isEqualTo(s);
        }
        assertThat(TaskStatus.fromWire(null)).isNull();
        assertThat(TaskStatus.fromWire("bogus")).isNull();
    }

    @Test
    public void taskStatus_terminalBoundary() {
        for (TaskStatus s : TaskStatus.values()) {
            boolean terminal =
                    s == TaskStatus.PUSHED
                            || s == TaskStatus.MERGED
                            || s == TaskStatus.FAILED
                            || s == TaskStatus.CANCELLED;
            assertThat(s.isTerminal()).isEqualTo(terminal);
        }
    }

    @Test
    public void taskPhase_roundTripsEveryWire() {
        for (TaskPhase p : TaskPhase.values()) {
            assertThat(TaskPhase.fromWire(p.wire())).isEqualTo(p);
        }
        assertThat(TaskPhase.fromWire("await_approval")).isEqualTo(TaskPhase.AWAIT_APPROVAL);
        assertThat(TaskPhase.fromWire(null)).isNull();
    }

    @Test
    public void changeStatus_decidedBoundary() {
        assertThat(ChangeStatus.fromWire("pending").isDecided()).isFalse();
        for (ChangeStatus s : new ChangeStatus[] {
                ChangeStatus.APPROVED, ChangeStatus.REJECTED,
                ChangeStatus.APPLIED, ChangeStatus.FAILED, ChangeStatus.SKIPPED}) {
            assertThat(s.isDecided()).isTrue();
        }
        assertThat(ChangeStatus.fromWire("approved")).isEqualTo(ChangeStatus.APPROVED);
    }
}