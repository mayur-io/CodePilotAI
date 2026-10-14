package dev.codepilotai.di;

import android.content.Context;
import dagger.Module;
import dagger.Provides;
import dev.codepilotai.data.db.AppDatabase;
import javax.inject.Singleton;

@Module
public final class DatabaseModule {

    @Provides
    @Singleton
    static AppDatabase provideDatabase(Context context) {
        return AppDatabase.get(context);
    }
}