package dev.codepilotai.data.repo;

import dev.codepilotai.data.api.ApiException;
import dev.codepilotai.data.api.AuthApi;
import dev.codepilotai.data.api.dto.AuthorizeUrlDto;
import dev.codepilotai.data.api.dto.TokenPairDto;
import dev.codepilotai.data.prefs.TokenStore;
import dev.codepilotai.domain.model.User;
import io.reactivex.rxjava3.core.Completable;
import io.reactivex.rxjava3.core.Single;
import java.util.Collections;

/** Sign-in state and token lifecycle. */
public final class AuthRepository {

    private final AuthApi api;
    private final TokenStore tokenStore;
    private final DevicesRepository devices;

    public AuthRepository(AuthApi api, TokenStore tokenStore, DevicesRepository devices) {
        this.api = api;
        this.tokenStore = tokenStore;
        this.devices = devices;
    }

    /** Kick off the OAuth dance: returns the GitHub URL to open in a Custom Tab. */
    public Single<String> authorizeUrl() {
        return api.authorizeStart()
                .map(dto -> dto.authorizeUrl)
                .onErrorResumeNext(t -> Single.error(ApiException.from(t)));
    }

    /** Exchange the one-time deep-link code for JWTs and persist them. */
    public Completable exchangeCode(String code) {
        return api.exchange(Collections.singletonMap("code", code))
                .flatMapCompletable(this::store)
                .onErrorResumeNext(t -> Completable.error(ApiException.from(t)));
    }

    private Completable store(TokenPairDto pair) {
        return Completable.fromAction(
                () -> tokenStore.save(pair.access, pair.refresh));
    }

    public Single<User> me() {
        return api.me()
                .map(dto -> dto.toDomain())
                .onErrorResumeNext(t -> Single.error(ApiException.from(t)));
    }

    public Completable logout() {
        String refresh = tokenStore.refreshToken() == null ? "" : tokenStore.refreshToken();
        // Drop this device's push registration first, while the access token is
        // still valid, so the next user of the device does not inherit alerts.
        return devices.unregisterCurrent()
                .andThen(api.logout(Collections.singletonMap("refresh", refresh)))
                .flatMapCompletable(
                        ignored -> Completable.fromAction(tokenStore::clear))
                .onErrorResumeNext(
                        t -> Completable.fromAction(tokenStore::clear));
    }

    public boolean isSignedIn() {
        return tokenStore.hasSession() && tokenStore.accessToken() != null;
    }

    public void clearLocalSession() {
        tokenStore.clear();
    }

    public TokenStore tokenStore() {
        return tokenStore;
    }
}