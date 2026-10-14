package dev.codepilotai.di;

import android.content.Context;
import dagger.Module;
import dagger.Provides;
import dev.codepilotai.data.prefs.TokenStore;
import javax.inject.Singleton;

@Module
public final class AppModule {

    @Provides
    @Singleton
    static Context provideContext(android.app.Application app) {
        return app;
    }

    @Provides
    @Singleton
    static TokenStore provideTokenStore(Context context) {
        return new TokenStore(context);
    }
}