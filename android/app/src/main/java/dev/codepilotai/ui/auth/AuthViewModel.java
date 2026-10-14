package dev.codepilotai.ui.auth;

import androidx.lifecycle.LiveData;
import androidx.lifecycle.MutableLiveData;
import androidx.lifecycle.ViewModel;
import androidx.lifecycle.ViewModelProvider;
import dev.codepilotai.data.repo.AuthRepository;
import dev.codepilotai.domain.model.User;
import dev.codepilotai.util.Event;
import io.reactivex.rxjava3.android.schedulers.AndroidSchedulers;
import io.reactivex.rxjava3.disposables.CompositeDisposable;
import io.reactivex.rxjava3.schedulers.Schedulers;

/** Sign-in state; the deep-link code lands here from MainActivity. */
public final class AuthViewModel extends ViewModel {

    public static final class State {
        public final boolean checking;
        public final boolean signedIn;
        public final String error;

        private State(boolean checking, boolean signedIn, String error) {
            this.checking = checking;
            this.signedIn = signedIn;
            this.error = error;
        }
    }

    private final AuthRepository auth;
    private final CompositeDisposable disposable = new CompositeDisposable();
    private final MutableLiveData<State> state = new MutableLiveData<>(new State(false, false, null));
    private final MutableLiveData<String> authorizeUrl = new MutableLiveData<>();
    private final Event<Boolean> signedIn = new Event<>();
    private final Event<String> signInFailed = new Event<>();

    public AuthViewModel(AuthRepository auth) {
        this.auth = auth;
    }

    public LiveData<State> state() {
        return state;
    }

    public LiveData<String> authorizeUrl() {
        return authorizeUrl;
    }

    public LiveData<Boolean> signedIn() {
        return signedIn.asLiveData();
    }

    public LiveData<String> signInFailed() {
        return signInFailed.asLiveData();
    }

    public void checkExistingSession() {
        boolean signedIn = auth.isSignedIn();
        state.setValue(new State(false, signedIn, null));
    }

    public boolean isSignedIn() {
        return auth.isSignedIn();
    }

    /** Fetch the GitHub authorize URL; the UI opens it in a Custom Tab. */
    public void beginSignIn() {
        disposable.add(
                auth.authorizeUrl()
                        .subscribeOn(Schedulers.io())
                        .observeOn(AndroidSchedulers.mainThread())
                        .subscribe(
                                url -> authorizeUrl.setValue(url),
                                t -> signInFailed.fire(t.getMessage())));
    }

    /** Exchange the one-time deep-link code and surface success/failure. */
    public void handleCode(String code) {
        state.setValue(new State(true, false, null));
        disposable.add(
                auth.exchangeCode(code)
                        .andThen(auth.me())
                        .subscribeOn(Schedulers.io())
                        .observeOn(AndroidSchedulers.mainThread())
                        .subscribe(
                                user -> {
                                    state.setValue(new State(false, true, null));
                                    signedIn.fire(true);
                                },
                                t -> {
                                    state.setValue(new State(false, false, t.getMessage()));
                                    signInFailed.fire(t.getMessage());
                                }));
    }

    public void clearError() {
        State s = state.getValue();
        if (s != null && s.error != null) {
            state.setValue(new State(s.checking, s.signedIn, null));
        }
    }

    @Override
    protected void onCleared() {
        disposable.clear();
    }

    /** Dagger-friendly factory. */
    public static final class Factory implements ViewModelProvider.Factory {
        private final AuthRepository auth;

        public Factory(AuthRepository auth) {
            this.auth = auth;
        }

        @SuppressWarnings("unchecked")
        @Override
        public <T extends ViewModel> T create(Class<T> modelClass) {
            return (T) new AuthViewModel(auth);
        }
    }
}