package dev.codepilotai.data.prefs;

import android.content.Context;
import android.content.SharedPreferences;
import androidx.security.crypto.EncryptedSharedPreferences;
import androidx.security.crypto.MasterKey;
import java.io.IOException;
import java.security.GeneralSecurityException;

/**
 * JWT storage at rest via {@link EncryptedSharedPreferences}. The access token
 * is short-lived (30 min server-side); on 401 the app re-runs the OAuth flow.
 */
public final class TokenStore {

    private static final String PREFS = "codepilotai.tokens";

    private final SharedPreferences prefs;

    public TokenStore(Context context) {
        SharedPreferences resolved;
        try {
            MasterKey masterKey = new MasterKey.Builder(context)
                    .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
                    .build();
            resolved = EncryptedSharedPreferences.create(
                    context,
                    PREFS,
                    masterKey,
                    EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                    EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM);
        } catch (GeneralSecurityException | IOException e) {
            // Fall back to a plain prefs file so a corrupted keystore never
            // bricks sign-in entirely; tokens are re-fetched on next auth.
            resolved = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        }
        prefs = resolved;
    }

    public void save(String access, String refresh) {
        prefs.edit()
                .putString("access", access)
                .putString("refresh", refresh)
                .putBoolean("has_session", true)
                .apply();
    }

    public String accessToken() {
        return prefs.getString("access", null);
    }

    public String refreshToken() {
        return prefs.getString("refresh", null);
    }

    public boolean hasSession() {
        return prefs.getBoolean("has_session", false);
    }

    public void clear() {
        prefs.edit().clear().apply();
    }
}