package dev.codepilotai.di;

import com.squareup.moshi.Moshi;
import dagger.Module;
import dagger.Provides;
import dev.codepilotai.BuildConfig;
import dev.codepilotai.data.api.AuthApi;
import dev.codepilotai.data.api.AuthInterceptor;
import dev.codepilotai.data.api.DevicesApi;
import dev.codepilotai.data.api.ReposApi;
import dev.codepilotai.data.api.SseClient;
import dev.codepilotai.data.api.TasksApi;
import dev.codepilotai.data.api.TokenRefresher;
import dev.codepilotai.data.prefs.TokenStore;
import java.util.concurrent.TimeUnit;
import javax.inject.Singleton;
import okhttp3.OkHttpClient;
import okhttp3.logging.HttpLoggingInterceptor;
import retrofit2.Retrofit;
import retrofit2.adapter.rxjava3.RxJava3CallAdapterFactory;
import retrofit2.converter.moshi.MoshiConverterFactory;

@Module
public final class NetworkModule {

    @Provides
    @Singleton
    static Moshi provideMoshi() {
        return new Moshi.Builder().build();
    }

    @Provides
    @Singleton
    static TokenRefresher provideTokenRefresher(TokenStore tokenStore) {
        return new TokenRefresher(tokenStore, BuildConfig.API_BASE_URL);
    }

    @Provides
    @Singleton
    static OkHttpClient provideOkHttpClient(TokenStore tokenStore, TokenRefresher refresher) {
        HttpLoggingInterceptor logging = new HttpLoggingInterceptor();
        logging.setLevel(HttpLoggingInterceptor.Level.BASIC);
        return new OkHttpClient.Builder()
                .connectTimeout(15, TimeUnit.SECONDS)
                .readTimeout(60, TimeUnit.SECONDS)
                .writeTimeout(30, TimeUnit.SECONDS)
                .addInterceptor(new AuthInterceptor(tokenStore, refresher))
                .addInterceptor(logging)
                .build();
    }

    @Provides
    @Singleton
    static AuthApi provideAuthApi(Retrofit retrofit) {
        return retrofit.create(AuthApi.class);
    }

    @Provides
    @Singleton
    static ReposApi provideReposApi(Retrofit retrofit) {
        return retrofit.create(ReposApi.class);
    }

    @Provides
    @Singleton
    static TasksApi provideTasksApi(Retrofit retrofit) {
        return retrofit.create(TasksApi.class);
    }

    @Provides
    @Singleton
    static DevicesApi provideDevicesApi(Retrofit retrofit) {
        return retrofit.create(DevicesApi.class);
    }

    @Provides
    @Singleton
    static SseClient provideSseClient(OkHttpClient client, Moshi moshi) {
        return new SseClient(client, moshi, BuildConfig.API_BASE_URL);
    }

    @Provides
    @Singleton
    static Retrofit provideRetrofit(OkHttpClient client, Moshi moshi) {
        return new Retrofit.Builder()
                .baseUrl(BuildConfig.API_BASE_URL.endsWith("/")
                        ? BuildConfig.API_BASE_URL
                        : BuildConfig.API_BASE_URL + "/")
                .client(client)
                .addConverterFactory(MoshiConverterFactory.create(moshi))
                .addCallAdapterFactory(RxJava3CallAdapterFactory.create())
                .build();
    }
}