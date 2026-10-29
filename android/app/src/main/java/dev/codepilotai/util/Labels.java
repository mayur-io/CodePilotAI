package dev.codepilotai.util;

import androidx.annotation.StringRes;
import dev.codepilotai.R;
import dev.codepilotai.domain.model.TaskPhase;
import dev.codepilotai.domain.model.TaskStatus;

/**
 * Maps machine wire values to a string resource id.
 *
 * <p>Returning a resource id rather than prose is what makes these labels
 * translatable: the UI resolves them with {@code getString(...)} against the
 * active locale, and pure-JVM callers (ViewModels) can pass the id straight
 * through without holding a {@code Context}.
 */
public final class Labels {

    private Labels() {}

    /** Streaming phase text for the run screen header (Task.md §9). */
    @StringRes
    public static int phaseLabelRes(TaskPhase phase) {
        if (phase == null) {
            return R.string.phase_working;
        }
        switch (phase) {
            case QUEUED: return R.string.phase_queued;
            case PLAN: return R.string.phase_planning;
            case CLARIFY: return R.string.phase_clarifying;
            case EXPLORE: return R.string.phase_reading_repo;
            case PROPOSE: return R.string.phase_drafting_changes;
            case AWAIT_APPROVAL: return R.string.phase_waiting_approval;
            case APPLY: return R.string.phase_applying_changes;
            case VERIFY: return R.string.phase_running_checks;
            case REPAIR: return R.string.phase_repairing;
            case COMMIT: return R.string.phase_committing;
            case PUSH: return R.string.phase_pushing;
            case DONE: return R.string.phase_done;
            default: return R.string.phase_working;
        }
    }

    /** Short status label for chips and the result header. */
    @StringRes
    public static int statusLabelRes(TaskStatus status) {
        if (status == null) {
            return R.string.status_unknown;
        }
        switch (status) {
            case DRAFT: return R.string.status_draft;
            case QUEUED: return R.string.status_queued;
            case CLARIFYING: return R.string.status_clarifying;
            case RUNNING: return R.string.status_running;
            case AWAITING_APPROVAL: return R.string.status_awaiting_approval;
            case APPLYING: return R.string.status_applying;
            case VERIFYING: return R.string.status_verifying;
            case PUSHED: return R.string.status_pushed;
            case MERGED: return R.string.status_merged;
            case FAILED: return R.string.status_failed;
            case CANCELLED: return R.string.status_cancelled;
            default: return R.string.status_unknown;
        }
    }
}
