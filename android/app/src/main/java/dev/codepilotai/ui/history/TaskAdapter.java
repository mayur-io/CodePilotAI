package dev.codepilotai.ui.history;

import android.view.LayoutInflater;
import android.view.ViewGroup;
import androidx.annotation.NonNull;
import androidx.recyclerview.widget.DiffUtil;
import androidx.recyclerview.widget.ListAdapter;
import androidx.recyclerview.widget.RecyclerView;
import dev.codepilotai.data.db.entity.TaskEntity;
import dev.codepilotai.databinding.ItemTaskBinding;
import dev.codepilotai.domain.model.TaskStatus;
import dev.codepilotai.util.Labels;

/** History row: task title, repo, status chip. */
public final class TaskAdapter extends ListAdapter<TaskEntity, TaskAdapter.Holder> {

    public interface Listener {
        void onTaskClicked(TaskEntity task);
    }

    private final Listener listener;

    public TaskAdapter(Listener listener) {
        super(new Diff());
        this.listener = listener;
    }

    @NonNull
    @Override
    public Holder onCreateViewHolder(@NonNull ViewGroup parent, int viewType) {
        ItemTaskBinding binding =
                ItemTaskBinding.inflate(LayoutInflater.from(parent.getContext()), parent, false);
        return new Holder(binding);
    }

    @Override
    public void onBindViewHolder(@NonNull Holder holder, int position) {
        TaskEntity task = getItem(position);
        holder.binding.titleText.setText(task.title != null && !task.title.trim().isEmpty() ? task.title : task.prompt);
        holder.binding.repoText.setText(task.repositoryFullName);
        holder.binding.timeText.setText(task.createdAt);
        TaskStatus status = TaskStatus.fromWire(task.status);
        holder.binding.statusChip.setText(Labels.statusLabelRes(status));
        int color = statusColor(holder, status);
        holder.binding.statusChip.setChipBackgroundColor(
                android.content.res.ColorStateList.valueOf(color));
        holder.binding.getRoot().setOnClickListener(v -> listener.onTaskClicked(task));
    }

    private static int statusColor(Holder holder, TaskStatus status) {
        android.content.Context c = holder.binding.getRoot().getContext();
        if (status == TaskStatus.FAILED || status == TaskStatus.CANCELLED) {
            return c.getColor(dev.codepilotai.R.color.rejected_red);
        }
        if (status == TaskStatus.PUSHED || status == TaskStatus.MERGED) {
            return c.getColor(dev.codepilotai.R.color.approved_green);
        }
        return c.getColor(dev.codepilotai.R.color.chip_blue);
    }

    static final class Holder extends RecyclerView.ViewHolder {
        final ItemTaskBinding binding;

        Holder(ItemTaskBinding binding) {
            super(binding.getRoot());
            this.binding = binding;
        }
    }

    private static final class Diff extends DiffUtil.ItemCallback<TaskEntity> {
        @Override
        public boolean areItemsTheSame(@NonNull TaskEntity a, @NonNull TaskEntity b) {
            return a.id == b.id;
        }

        @Override
        public boolean areContentsTheSame(@NonNull TaskEntity a, @NonNull TaskEntity b) {
            return java.util.Objects.equals(a.status, b.status)
                    && java.util.Objects.equals(a.prUrl, b.prUrl)
                    && java.util.Objects.equals(a.updatedAt, b.updatedAt);
        }
    }
}