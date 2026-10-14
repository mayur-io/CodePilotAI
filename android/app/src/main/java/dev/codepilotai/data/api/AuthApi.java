package dev.codepilotai.data.api;

import dev.codepilotai.data.api.dto.AuthorizeUrlDto;
import dev.codepilotai.data.api.dto.FollowUpRequest;
import dev.codepilotai.data.api.dto.LogoutResultDto;
import dev.codepilotai.data.api.dto.TokenPairDto;
import io.reactivex.rxjava3.core.Single;
import java.util.Map;
import retrofit2.http.Body;
import retrofit2.http.GET;
import retrofit2.http.POST;

/** The accounts API: OAuth sign-in and token exchange. */
public interface AuthApi {

    @GET("auth/github/start")
    Single<AuthorizeUrlDto> authorizeStart();

    /** Exchange the one-time deep-link code for JWTs. No auth required. */
    @POST("auth/exchange")
    Single<TokenPairDto> exchange(@Body Map<String, String> code);

    @GET("auth/me")
    Single<dev.codepilotai.data.api.dto.UserDto> me();

    @POST("auth/logout")
    Single<LogoutResultDto> logout(@Body Map<String, String> refresh);
}