package dev.codepilotai.util;

import static com.google.common.truth.Truth.assertThat;

import dev.codepilotai.R;
import dev.codepilotai.domain.model.TaskPhase;
import dev.codepilotai.domain.model.TaskStatus;
import org.junit.Test;

/** The string-resource labels the run/result/history screens display. */
public final class LabelsTest {

    @Test
    public void phaseLabelRes_coversEveryPhase() {
        assertThat(Labels.phaseLabelRes(TaskPhase.QUEUED)).isEqualTo(R.string.phase_queued);
        assertThat(Labels.phaseLabelRes(TaskPhase.PLAN)).isEqualTo(R.string.phase_planning);
        assertThat(Labels.phaseLabelRes(TaskPhase.PROPOSE))
                .isEqualTo(R.string.phase_drafting_changes);
        assertThat(Labels.phaseLabelRes(TaskPhase.AWAIT_APPROVAL))
                .isEqualTo(R.string.phase_waiting_approval);
        assertThat(Labels.phaseLabelRes(TaskPhase.REPAIR)).isEqualTo(R.string.phase_repairing);
        assertThat(Labels.phaseLabelRes(TaskPhase.DONE)).isEqualTo(R.string.phase_done);
        // Distinct phases must not collapse to the same resource.
        assertThat(Labels.phaseLabelRes(TaskPhase.QUEUED))
                .isNotEqualTo(Labels.phaseLabelRes(TaskPhase.PLAN));
    }

    @Test
    public void phaseLabelRes_everyPhaseIsMapped() {
        for (TaskPhase phase : TaskPhase.values()) {
            assertThat(Labels.phaseLabelRes(phase)).isNotEqualTo(0);
        }
    }

    @Test
    public void phaseLabelRes_null_isWorking() {
        assertThat(Labels.phaseLabelRes(null)).isEqualTo(R.string.phase_working);
    }

    @Test
    public void statusLabelRes_coversKeyStates() {
        assertThat(Labels.statusLabelRes(TaskStatus.AWAITING_APPROVAL))
                .isEqualTo(R.string.status_awaiting_approval);
        assertThat(Labels.statusLabelRes(TaskStatus.PUSHED)).isEqualTo(R.string.status_pushed);
        assertThat(Labels.statusLabelRes(TaskStatus.MERGED)).isEqualTo(R.string.status_merged);
        assertThat(Labels.statusLabelRes(TaskStatus.FAILED)).isEqualTo(R.string.status_failed);
        assertThat(Labels.statusLabelRes(TaskStatus.CANCELLED))
                .isEqualTo(R.string.status_cancelled);
        for (TaskStatus status : TaskStatus.values()) {
            assertThat(Labels.statusLabelRes(status)).isNotEqualTo(0);
        }
    }

    @Test
    public void statusLabelRes_null_isUnknown() {
        assertThat(Labels.statusLabelRes(null)).isEqualTo(R.string.status_unknown);
    }
}
