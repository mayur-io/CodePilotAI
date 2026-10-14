package dev.codepilotai.data.api;

import dev.codepilotai.data.api.dto.BranchDto;
import dev.codepilotai.data.api.dto.CreateRepositoryRequest;
import dev.codepilotai.data.api.dto.Page;
import dev.codepilotai.data.api.dto.RepositoryDto;
import dev.codepilotai.data.api.dto.SyncResultDto;
import io.reactivex.rxjava3.core.Single;
import java.util.List;
import retrofit2.http.Body;
import retrofit2.http.GET;
import retrofit2.http.POST;
import retrofit2.http.Path;

/** The repositories API. */
public interface ReposApi {

    @GET("repos/")
    Single<Page<RepositoryDto>> list();

    @POST("repos/")
    Single<RepositoryDto> create(@Body CreateRepositoryRequest request);

    @POST("repos/sync/")
    Single<SyncResultDto> sync();

    @GET("repos/{id}/branches/")
    Single<List<BranchDto>> branches(@Path("id") long id);
}