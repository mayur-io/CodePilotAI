package dev.codepilotai.util;

import androidx.lifecycle.LiveData;
import androidx.lifecycle.MutableLiveData;

/**
 * A {@link LiveData} that emits each value exactly once, so navigation or toasts
 * are not replayed across configuration changes.
 */
public final class Event<T> {

    private final MutableLiveData<T> liveData = new MutableLiveData<>();

    public void fire(T value) {
        liveData.setValue(value);
    }

    public LiveData<T> asLiveData() {
        return liveData;
    }
}