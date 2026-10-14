package dev.codepilotai.data.repo;

import static com.google.common.truth.Truth.assertThat;

import dev.codepilotai.data.db.entity.ChangeEntity;
import dev.codepilotai.domain.model.ProposedChange;
import org.junit.Test;

/** The pure Room-entity → domain mapping used by the approval screen. */
public final class TaskRepositoryMappingTest {

    private static ChangeEntity entity(String status) {
        ChangeEntity e = new ChangeEntity();
        e.id = 7;
        e.taskId = 42;
        e.orderIndex = 2;
        e.path = "src/Main.java";
        e.changeType = "modify";
        e.patch = "@@ -1 +1 @@\n-old\n+new\n";
        e.rationale = "Because.";
        e.additions = 1;
        e.deletions = 1;
        e.status = status;
        e.createdAt = "2025-01-01T00:00:00Z";
        return e;
    }

    @Test
    public void toDomain_pendingChange_isNotDecided() {
        ProposedChange c = TaskRepository.toDomain(entity("pending"));
        assertThat(c.id).isEqualTo(7L);
        assertThat(c.taskId).isEqualTo(42L);
        assertThat(c.orderIndex).isEqualTo(2);
        assertThat(c.path).isEqualTo("src/Main.java");
        assertThat(c.changeType).isEqualTo("modify");
        assertThat(c.patch).contains("+new");
        assertThat(c.rationale).isEqualTo("Because.");
        assertThat(c.additions).isEqualTo(1);
        assertThat(c.deletions).isEqualTo(1);
        assertThat(c.status).isEqualTo("pending");
        assertThat(c.decided).isFalse();
    }

    @Test
    public void toDomain_decidedChange_flipsFlag() {
        assertThat(TaskRepository.toDomain(entity("approved")).decided).isTrue();
        assertThat(TaskRepository.toDomain(entity("rejected")).decided).isTrue();
        assertThat(TaskRepository.toDomain(entity("applied")).decided).isTrue();
    }
}