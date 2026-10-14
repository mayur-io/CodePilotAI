package dev.codepilotai.data.db.entity;

import androidx.room.Entity;

/** One event from a task's stream, cache-side (payload kept as raw JSON). */
@Entity(tableName = "run_events", primaryKeys = {"taskId", "seq"})
public final class EventEntity {

    public long taskId;
    public long seq;
    public String kind;
    public String payloadJson;
    public String createdAt;

    public EventEntity() {}
}