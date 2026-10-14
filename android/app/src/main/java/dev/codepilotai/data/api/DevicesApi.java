package dev.codepilotai.data.api;

import dev.codepilotai.data.api.dto.DeviceTokenDto;
import dev.codepilotai.data.api.dto.DeviceTokenRequest;
import io.reactivex.rxjava3.core.Completable;
import io.reactivex.rxjava3.core.Single;
import java.util.Map;
import retrofit2.http.Body;
import retrofit2.http.POST;

/** Push-device registration, mirroring the backend notifications API. */
public interface DevicesApi {

    /** Upsert this install's FCM token. 201 for a new token, 200 for a refresh. */
    @POST("devices")
    Single<DeviceTokenDto> register(@Body DeviceTokenRequest request);

    /** Drop the token. 204 No Content, so the {@link Completable} has no body. */
    @POST("devices/unregister")
    Completable unregister(@Body Map<String, String> token);
}
