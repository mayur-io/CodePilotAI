package dev.codepilotai;

import android.app.Application;
import androidx.work.Configuration;
import dev.codepilotai.di.AppComponent;
import dev.codepilotai.di.DaggerAppComponent;
import dev.codepilotai.work.CodePilotWorkerFactory;

/** Application singleton; owns the Dagger graph and WorkManager's factory. */
public final class CodePilotApp extends Application implements Configuration.Provider {

    private AppComponent component;

    @Override
    public void onCreate() {
        super.onCreate();
        component = DaggerAppComponent.builder().application(this).build();
        component.inject(this);
    }

    public AppComponent component() {
        return component;
    }

    @Override
    public Configuration getWorkManagerConfiguration() {
        return new Configuration.Builder()
                .setWorkerFactory(component.workerFactory())
                .build();
    }
}