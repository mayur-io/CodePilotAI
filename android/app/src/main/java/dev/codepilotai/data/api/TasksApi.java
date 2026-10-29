package dev.codepilotai.data.api;

import dev.codepilotai.data.api.dto.ChangeDecisionRequest;
import dev.codepilotai.data.api.dto.ChangeDto;
import dev.codepilotai.data.api.dto.ClarificationAnswersRequest;
import dev.codepilotai.data.api.dto.ClarificationDto;
import dev.codepilotai.data.api.dto.CreateTaskRequest;
import dev.codepilotai.data.api.dto.FollowUpRequest;
import dev.codepilotai.data.api.dto.MessageDto;
import dev.codepilotai.data.api.dto.Page;
import dev.codepilotai.data.api.dto.RunEventDto;
import dev.codepilotai.data.api.dto.TaskDetailDto;
import io.reactivex.rxjava3.core.Single;
import java.util.List;
import retrofit2.http.Body;
import retrofit2.http.GET;
import retrofit2.http.POST;
import retrofit2.http.Path;
import retrofit2.http.Query;

/** The agent-tasks API, mirroring the backend viewset routes. */
public interface TasksApi {

    @POST("tasks/")
    Single<TaskDetailDto> create(@Body CreateTaskRequest request);

    @GET("tasks/")
    Single<Page<TaskDetailDto>> list();

    @GET("tasks/{id}/")
    Single<TaskDetailDto> detail(@Path("id") long id);

    @GET("tasks/{id}/changes/")
    Single<List<ChangeDto>> changes(@Path("id") long id);

    @GET("tasks/{id}/questions/")
    Single<List<ClarificationDto>> questions(@Path("id") long id);

    @POST("tasks/{id}/questions/answer/")
    Single<List<ClarificationDto>> answerQuestions(
            @Path("id") long id, @Body ClarificationAnswersRequest request);

    @POST("tasks/{id}/changes/{changeId}/decide/")
    Single<ChangeDto> decide(
            @Path("id") long id,
            @Path("changeId") long changeId,
            @Body ChangeDecisionRequest request);

    @POST("tasks/{id}/execute/")
    Single<TaskDetailDto> execute(@Path("id") long id);

    @GET("tasks/{id}/messages/")
    Single<List<MessageDto>> messages(@Path("id") long id);

    @POST("tasks/{id}/messages/")
    Single<MessageDto> postMessage(@Path("id") long id, @Body FollowUpRequest request);

    @POST("tasks/{id}/cancel/")
    Single<TaskDetailDto> cancel(@Path("id") long id);

    @GET("tasks/{id}/events/")
    Single<List<RunEventDto>> events(@Path("id") long id, @Query("after_seq") long afterSeq);
}