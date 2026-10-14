package dev.codepilotai.ui.repos;

import android.view.LayoutInflater;
import android.view.ViewGroup;
import androidx.annotation.NonNull;
import androidx.recyclerview.widget.DiffUtil;
import androidx.recyclerview.widget.ListAdapter;
import androidx.recyclerview.widget.RecyclerView;
import dev.codepilotai.data.db.entity.RepositoryEntity;
import dev.codepilotai.databinding.ItemRepoBinding;

/** Repository row for the picker list. */
public final class RepoAdapter extends ListAdapter<RepositoryEntity, RepoAdapter.Holder> {

    public interface Listener {
        void onRepoClicked(RepositoryEntity repo);
    }

    private final Listener listener;

    public RepoAdapter(Listener listener) {
        super(new Diff());
        this.listener = listener;
    }

    @NonNull
    @Override
    public Holder onCreateViewHolder(@NonNull ViewGroup parent, int viewType) {
        ItemRepoBinding binding =
                ItemRepoBinding.inflate(LayoutInflater.from(parent.getContext()), parent, false);
        return new Holder(binding);
    }

    @Override
    public void onBindViewHolder(@NonNull Holder holder, int position) {
        RepositoryEntity repo = getItem(position);
        holder.binding.nameText.setText(repo.fullName != null ? repo.fullName : repo.name);
        holder.binding.detailText.setText(repo.description);
        holder.binding.languageText.setText(repo.primaryLanguage);
        holder.binding.taskCountText.setText(String.valueOf(repo.taskCount));
        holder.binding.getRoot().setOnClickListener(v -> listener.onRepoClicked(repo));
    }

    static final class Holder extends RecyclerView.ViewHolder {
        final ItemRepoBinding binding;

        Holder(ItemRepoBinding binding) {
            super(binding.getRoot());
            this.binding = binding;
        }
    }

    private static final class Diff extends DiffUtil.ItemCallback<RepositoryEntity> {
        @Override
        public boolean areItemsTheSame(@NonNull RepositoryEntity a, @NonNull RepositoryEntity b) {
            return a.id == b.id;
        }

        @Override
        public boolean areContentsTheSame(@NonNull RepositoryEntity a, @NonNull RepositoryEntity b) {
            return a.taskCount == b.taskCount
                    && java.util.Objects.equals(a.fullName, b.fullName)
                    && java.util.Objects.equals(a.description, b.description);
        }
    }
}