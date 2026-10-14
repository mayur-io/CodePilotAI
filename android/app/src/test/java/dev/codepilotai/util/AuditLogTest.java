package dev.codepilotai.util;

import static com.google.common.truth.Truth.assertThat;

import dev.codepilotai.R;
import dev.codepilotai.domain.model.RunEvent;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.Map;
import org.junit.Test;

/**
 * The audit-trail formatter: decisions must name their actor, logs stay out, and
 * every line must be a translatable resource reference (never baked-in English).
 */
public final class AuditLogTest {

    /** Fake resolver that renders the id and its args so composition is observable. */
    private static final AuditLog.Lookup LOOKUP =
            (resId, args) -> {
                StringBuilder sb = new StringBuilder("<").append(resId);
                for (Object arg : args) {
                    sb.append(',').append(arg);
                }
                return sb.append('>').toString();
            };

    private static RunEvent event(long seq, String kind, Map<String, Object> payload) {
        return new RunEvent(seq, kind, payload, "2026-10-11T09:30:00Z");
    }

    private static Map<String, Object> payload(Object... kv) {
        Map<String, Object> map = new HashMap<>();
        for (int i = 0; i < kv.length; i += 2) {
            map.put((String) kv[i], kv[i + 1]);
        }
        return map;
    }

    @Test
    public void filtered_dropsLogAndReasoning() {
        RunEvent status = event(1, "status", payload("status", "running"));
        RunEvent log = event(2, "log", payload("message", "noise"));
        RunEvent reasoning = event(3, "reasoning", payload("text", "thinking"));
        RunEvent decided = event(4, "change_decided", payload("decision", "approve"));

        assertThat(AuditLog.filtered(Arrays.asList(status, log, reasoning, decided)))
                .containsExactly(status, decided)
                .inOrder();
        assertThat(AuditLog.filtered(null)).isEmpty();
        assertThat(AuditLog.filtered(Collections.emptyList())).isEmpty();
    }

    @Test
    public void summary_decisionNamesActorAndPath() {
        RunEvent decided = event(
                1,
                "change_decided",
                payload(
                        "path", "app/views.py",
                        "decision", "approve",
                        "actor_username", "carol"));
        AuditLine line = AuditLog.summary(decided);
        assertThat(line.resId).isEqualTo(R.string.audit_decision_actor);
        AuditLine subject = (AuditLine) line.args[0];
        assertThat(subject.resId).isEqualTo(0);
        assertThat(subject.raw).isEqualTo("app/views.py");
        assertThat(((AuditLine) line.args[1]).resId).isEqualTo(R.string.audit_approved);
        assertThat(line.args[2]).isEqualTo("carol");
    }

    @Test
    public void summary_decisionWithoutActorOmitsActor() {
        RunEvent decided = event(1, "change_decided", payload("path", "a.py", "decision", "reject"));
        AuditLine line = AuditLog.summary(decided);
        assertThat(line.resId).isEqualTo(R.string.audit_decision);
        assertThat(((AuditLine) line.args[1]).resId).isEqualTo(R.string.audit_rejected);
    }

    @Test
    public void summary_editRequestVerb() {
        RunEvent decided =
                event(1, "change_decided", payload("path", "a.py", "decision", "request_edit"));
        AuditLine line = AuditLog.summary(decided);
        assertThat(((AuditLine) line.args[1]).resId).isEqualTo(R.string.audit_edit_requested);
    }

    @Test
    public void summary_verificationCountsPassedOutcomes() {
        RunEvent verification = event(
                1,
                "verification",
                payload(
                        "passed", true,
                        "outcomes",
                        Arrays.asList(
                                payload("command", "ruff", "passed", true),
                                payload("command", "pytest", "passed", false))));
        AuditLine line = AuditLog.summary(verification);
        assertThat(line.resId).isEqualTo(R.string.audit_verification_checks);
        assertThat(line.args).asList().containsExactly(1, 2).inOrder();
    }

    @Test
    public void summary_verificationWithoutChecksUsesPassedFlag() {
        RunEvent verification = event(1, "verification", payload("passed", false));
        assertThat(AuditLog.summary(verification).resId).isEqualTo(R.string.audit_verification_failed);
    }

    @Test
    public void summary_statusAndPhaseEmbedLocalizedLabels() {
        AuditLine status = AuditLog.summary(event(1, "status", payload("status", "awaiting_approval")));
        assertThat(status.resId).isEqualTo(R.string.audit_status);
        assertThat(((AuditLine) status.args[0]).resId).isEqualTo(R.string.status_awaiting_approval);

        AuditLine phase = AuditLog.summary(event(2, "phase", payload("phase", "verify")));
        assertThat(phase.resId).isEqualTo(R.string.audit_phase);
        assertThat(((AuditLine) phase.args[0]).resId).isEqualTo(R.string.phase_running_checks);
    }

    @Test
    public void summary_pushedIncludesBranchAndUrl() {
        RunEvent pushed = event(
                1, "pushed", payload("branch", "codepilot/x", "pr_url", "https://gh/pr/1"));
        AuditLine line = AuditLog.summary(pushed);
        assertThat(line.resId).isEqualTo(R.string.audit_pushed_url);
        AuditLine base = (AuditLine) line.args[0];
        assertThat(base.resId).isEqualTo(R.string.audit_pushed_branch);
        assertThat(base.args[0]).isEqualTo("codepilot/x");
        assertThat(line.args[1]).isEqualTo("https://gh/pr/1");
    }

    @Test
    public void summary_pushedWithoutUrlIsJustTheBranch() {
        RunEvent pushed = event(1, "pushed", payload("branch", "codepilot/x"));
        assertThat(AuditLog.summary(pushed).resId).isEqualTo(R.string.audit_pushed_branch);
    }

    @Test
    public void summary_knownButUnlistedKindFallsBackToRawMessage() {
        // LOG is a real kind but not part of the audit switch, so the default branch runs.
        RunEvent log = event(1, "log", payload("message", "verbatim"));
        AuditLine line = AuditLog.summary(log);
        assertThat(line.resId).isEqualTo(0);
        assertThat(AuditLog.render(line, LOOKUP)).isEqualTo("verbatim");
    }

    @Test
    public void summary_unknownWireKindFallsBackToTheKindItself() {
        RunEvent mystery = event(1, "mystery", payload("message", "ignored"));
        assertThat(AuditLog.render(AuditLog.summary(mystery), LOOKUP)).isEqualTo("mystery");
    }

    @Test
    public void render_resolvesNestedLines() {
        AuditLine line = AuditLog.summary(event(1, "status", payload("status", "awaiting_approval")));
        assertThat(AuditLog.render(line, LOOKUP))
                .isEqualTo(
                        "<" + R.string.audit_status + ",<" + R.string.status_awaiting_approval + ">>");
    }

    @Test
    public void timestamp_trimsToMinutes() {
        assertThat(AuditLog.timestamp("2026-10-11T09:30:42.123456Z")).isEqualTo("2026-10-11 09:30");
        assertThat(AuditLog.timestamp(null)).isEmpty();
    }
}
