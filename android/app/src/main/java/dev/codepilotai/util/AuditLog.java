package dev.codepilotai.util;

import dev.codepilotai.R;
import dev.codepilotai.domain.model.RunEvent;
import dev.codepilotai.domain.model.TaskPhase;
import dev.codepilotai.domain.model.TaskStatus;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * Human-readable rendering for the task audit trail on the result screen.
 *
 * <p>The raw event stream already carries everything an audit log needs — every
 * decision with its {@code actor_username} and {@code decided_at}, verification
 * outcomes, pushes — so this is a pure formatting layer over {@link RunEvent}.
 * It deliberately excludes the high-volume {@code log} and {@code reasoning}
 * frames, which belong to the live run screen rather than the audit record.
 *
 * <p>Rather than baking in English prose, {@link #summary} returns an
 * {@link AuditLine} (a string-resource id plus args); {@link #render} resolves
 * it against a caller-supplied {@link Lookup}, so the same logic works with any
 * locale and stays testable without a {@code Context}.
 */
public final class AuditLog {

    private AuditLog() {}

    /** Resolves a string resource id (with args) to text in the active locale. */
    public interface Lookup {
        String get(int resId, Object[] args);
    }

    /** Whether an event belongs in the audit trail (lifecycle + decisions + checks). */
    public static boolean isAuditKind(RunEvent event) {
        RunEvent.Kind kind = event.kindEnum();
        if (kind == null) {
            return false;
        }
        switch (kind) {
            case STATUS:
            case PHASE:
            case CHANGE_PROPOSED:
            case CHANGE_DECIDED:
            case VERIFICATION:
            case PUSHED:
            case MESSAGE:
            case ERROR:
            case DONE:
                return true;
            default:
                return false;
        }
    }

    /** Keep only audit-relevant events, preserving their chronological order. */
    public static List<RunEvent> filtered(List<RunEvent> events) {
        List<RunEvent> out = new ArrayList<>();
        if (events == null) {
            return out;
        }
        for (RunEvent event : events) {
            if (isAuditKind(event)) {
                out.add(event);
            }
        }
        return out;
    }

    /**
     * Resolve a line (and any nested lines) to text using {@code lookup}. In the
     * app, pass {@code (id, args) -> context.getString(id, args)}.
     */
    public static String render(AuditLine line, Lookup lookup) {
        if (line == null) {
            return "";
        }
        if (line.resId == 0) {
            return line.raw;
        }
        Object[] resolved = new Object[line.args.length];
        for (int i = 0; i < line.args.length; i++) {
            Object arg = line.args[i];
            resolved[i] = arg instanceof AuditLine ? render((AuditLine) arg, lookup) : arg;
        }
        return lookup.get(line.resId, resolved);
    }

    /** One line describing an event, with actor attribution for decisions. */
    public static AuditLine summary(RunEvent event) {
        Map<String, Object> p = event.payload;
        RunEvent.Kind kind = event.kindEnum();
        if (kind == null) {
            return AuditLine.raw(event.kind);
        }
        switch (kind) {
            case CHANGE_DECIDED:
                return decisionLine(p);
            case CHANGE_PROPOSED:
                return AuditLine.of(
                        R.string.audit_proposed,
                        str(p, "path"),
                        number(p, "additions"),
                        number(p, "deletions"));
            case STATUS:
                return AuditLine.of(
                        R.string.audit_status,
                        AuditLine.of(Labels.statusLabelRes(TaskStatus.fromWire(str(p, "status")))));
            case PHASE:
                return AuditLine.of(
                        R.string.audit_phase,
                        AuditLine.of(Labels.phaseLabelRes(TaskPhase.fromWire(str(p, "phase")))));
            case VERIFICATION:
                return verificationLine(p);
            case PUSHED: {
                String branch = str(p, "branch");
                String url = str(p, "pr_url");
                AuditLine base =
                        branch.isEmpty()
                                ? AuditLine.of(R.string.audit_pushed)
                                : AuditLine.of(R.string.audit_pushed_branch, branch);
                return url.isEmpty() ? base : AuditLine.of(R.string.audit_pushed_url, base, url);
            }
            case MESSAGE: {
                String role = str(p, "role");
                String content = trim(str(p, "content"), 200);
                return role.isEmpty()
                        ? AuditLine.of(R.string.audit_message_plain, content)
                        : AuditLine.of(R.string.audit_message, role, content);
            }
            case ERROR: {
                String message = firstNonEmpty(str(p, "error_message"), str(p, "message"));
                return message.isEmpty()
                        ? AuditLine.of(R.string.audit_error)
                        : AuditLine.of(R.string.audit_error_detail, message);
            }
            case DONE:
                return AuditLine.of(
                        R.string.audit_finished,
                        AuditLine.of(Labels.statusLabelRes(TaskStatus.fromWire(str(p, "status")))));
            default:
                return AuditLine.raw(str(p, "message"));
        }
    }

    /** ISO-8601 to {@code "YYYY-MM-DD HH:MM"} without java.time (minSdk 24). */
    public static String timestamp(String iso) {
        if (iso == null) {
            return "";
        }
        String value = iso.length() > 19 ? iso.substring(0, 19) : iso;
        value = value.replace('T', ' ');
        return value.length() > 16 ? value.substring(0, 16) : value;
    }

    private static AuditLine decisionLine(Map<String, Object> p) {
        String path = str(p, "path");
        String actor = str(p, "actor_username");
        AuditLine subject = path.isEmpty() ? AuditLine.of(R.string.audit_change) : AuditLine.raw(path);
        AuditLine verb = decisionVerb(str(p, "decision"));
        return actor.isEmpty()
                ? AuditLine.of(R.string.audit_decision, subject, verb)
                : AuditLine.of(R.string.audit_decision_actor, subject, verb, actor);
    }

    private static AuditLine decisionVerb(String decision) {
        if (decision == null) {
            return AuditLine.of(R.string.audit_decided);
        }
        switch (decision) {
            case "approve":
                return AuditLine.of(R.string.audit_approved);
            case "reject":
                return AuditLine.of(R.string.audit_rejected);
            case "request_edit":
                return AuditLine.of(R.string.audit_edit_requested);
            default:
                return AuditLine.raw(decision);
        }
    }

    private static AuditLine verificationLine(Map<String, Object> p) {
        String note = str(p, "note");
        if (!note.isEmpty()) {
            return AuditLine.of(R.string.audit_verification_note, note);
        }
        List<?> outcomes = asList(p, "outcomes");
        if (outcomes != null && !outcomes.isEmpty()) {
            int passed = 0;
            for (Object outcome : outcomes) {
                if (outcome instanceof Map && Boolean.TRUE.equals(((Map<?, ?>) outcome).get("passed"))) {
                    passed++;
                }
            }
            return AuditLine.of(R.string.audit_verification_checks, passed, outcomes.size());
        }
        return AuditLine.of(
                Boolean.TRUE.equals(asBool(p, "passed"))
                        ? R.string.audit_verification_passed
                        : R.string.audit_verification_failed);
    }

    private static String str(Map<String, Object> p, String key) {
        if (p == null) {
            return "";
        }
        Object value = p.get(key);
        return value == null ? "" : String.valueOf(value);
    }

    private static String number(Map<String, Object> p, String key) {
        if (p == null) {
            return "0";
        }
        Object value = p.get(key);
        if (value instanceof Number) {
            double d = ((Number) value).doubleValue();
            return d == Math.rint(d) ? String.valueOf((long) d) : String.valueOf(d);
        }
        return value == null ? "0" : String.valueOf(value);
    }

    private static Boolean asBool(Map<String, Object> p, String key) {
        if (p == null) {
            return null;
        }
        Object value = p.get(key);
        return value instanceof Boolean ? (Boolean) value : null;
    }

    private static List<?> asList(Map<String, Object> p, String key) {
        if (p == null) {
            return null;
        }
        Object value = p.get(key);
        return value instanceof List ? (List<?>) value : null;
    }

    private static String firstNonEmpty(String a, String b) {
        return a != null && !a.isEmpty() ? a : (b == null ? "" : b);
    }

    private static String trim(String s, int max) {
        if (s == null) {
            return "";
        }
        return s.length() > max ? s.substring(0, max) + "\u2026" : s;
    }
}
