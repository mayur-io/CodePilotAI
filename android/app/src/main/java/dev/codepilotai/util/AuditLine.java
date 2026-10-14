package dev.codepilotai.util;

import androidx.annotation.StringRes;

/**
 * One localizable output line: either a string resource with format args, or a
 * verbatim string when no resource fits (unknown wire values, user text).
 *
 * <p>An argument may itself be an {@link AuditLine}, which lets a line embed
 * another translated label (e.g. {@code "Status → %1$s"} wrapping the localized
 * status name). {@link AuditLog#render} resolves the whole tree.
 */
public final class AuditLine {

    /** {@code 0} when the line is verbatim text rather than a string resource. */
    public final int resId;

    /** Format arguments; elements may be {@link AuditLine} instances. */
    public final Object[] args;

    /** Verbatim text, used only when {@link #resId} is {@code 0}. */
    public final String raw;

    private AuditLine(int resId, Object[] args, String raw) {
        this.resId = resId;
        this.args = args;
        this.raw = raw;
    }

    public static AuditLine of(@StringRes int resId, Object... args) {
        return new AuditLine(resId, args == null ? new Object[0] : args, null);
    }

    public static AuditLine raw(String text) {
        return new AuditLine(0, new Object[0], text == null ? "" : text);
    }
}
