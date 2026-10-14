package dev.codepilotai.ui.approval;

import android.content.Context;
import android.view.LayoutInflater;
import android.view.ViewGroup;
import android.annotation.SuppressLint;
import androidx.annotation.NonNull;
import androidx.recyclerview.widget.RecyclerView;
import dev.codepilotai.R;
import dev.codepilotai.databinding.ItemDiffLineBinding;
import dev.codepilotai.domain.model.DiffLine;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * Renders one file's parsed diff. Hunk headers are collapsible (tap to
 * expand/collapse), additions and deletions are highlighted (Task.md §9).
 *
 * <p>Hunk-collapse toggles and file switches rebind the whole (small) list, so plain
 * {@code notifyDataSetChanged} is intentional here — DiffUtil would not pay for itself.
 */
@SuppressLint("NotifyDataSetChanged")
public final class DiffLineAdapter extends RecyclerView.Adapter<DiffLineAdapter.Holder> {

    private final List<DiffLine> lines = new ArrayList<>();
    private final List<Integer> visible = new ArrayList<>();
    private final Set<Integer> collapsedHunks = new HashSet<>();
    private final boolean dark;

    public DiffLineAdapter(boolean dark) {
        this.dark = dark;
        setHasStableIds(true);
    }

    public void submit(List<DiffLine> diffLines) {
        lines.clear();
        if (diffLines != null) {
            lines.addAll(diffLines);
        }
        rebuildVisible();
    }

    private void rebuildVisible() {
        visible.clear();
        boolean hidden = false;
        for (int i = 0; i < lines.size(); i++) {
            DiffLine line = lines.get(i);
            if (line.kind == DiffLine.Kind.HUNK_HEADER) {
                hidden = collapsedHunks.contains(i);
                visible.add(i);
            } else if (line.kind == DiffLine.Kind.FILE_HEADER) {
                hidden = false;
                visible.add(i);
            } else if (!hidden) {
                visible.add(i);
            }
        }
        notifyDataSetChanged();
    }

    private void toggleHunk(int adapterPosition) {
        if (adapterPosition < 0 || adapterPosition >= visible.size()) {
            return;
        }
        int lineIndex = visible.get(adapterPosition);
        if (collapsedHunks.contains(lineIndex)) {
            collapsedHunks.remove(lineIndex);
        } else {
            collapsedHunks.add(lineIndex);
        }
        rebuildVisible();
    }

    @Override
    public long getItemId(int position) {
        return visible.get(position);
    }

    @NonNull
    @Override
    public Holder onCreateViewHolder(@NonNull ViewGroup parent, int viewType) {
        ItemDiffLineBinding binding =
                ItemDiffLineBinding.inflate(LayoutInflater.from(parent.getContext()), parent, false);
        return new Holder(binding);
    }

    @Override
    public void onBindViewHolder(@NonNull Holder holder, int position) {
        DiffLine line = lines.get(visible.get(position));
        Context c = holder.itemView.getContext();
        holder.binding.lineText.setText(line.text == null ? "" : line.text);
        holder.binding.lineText.setTextColor(textColor(c, line));
        holder.binding.lineText.setBackgroundColor(background(c, line));
        holder.binding.lineText.setOnClickListener(null);
        if (line.kind == DiffLine.Kind.HUNK_HEADER) {
            holder.binding.lineText.setOnClickListener(
                    v -> toggleHunk(holder.getBindingAdapterPosition()));
        }
    }

    @Override
    public int getItemCount() {
        return visible.size();
    }

    private int background(Context c, DiffLine line) {
        if (dark) {
            switch (line.kind) {
                case ADDED: return c.getColor(R.color.diff_dark_add_bg);
                case REMOVED: return c.getColor(R.color.diff_dark_del_bg);
                case HUNK_HEADER: return c.getColor(R.color.diff_dark_hunk_bg);
                default: return android.graphics.Color.TRANSPARENT;
            }
        }
        switch (line.kind) {
            case ADDED: return c.getColor(R.color.diff_add_bg);
            case REMOVED: return c.getColor(R.color.diff_del_bg);
            case HUNK_HEADER: return c.getColor(R.color.diff_hunk_bg);
            default: return android.graphics.Color.TRANSPARENT;
        }
    }

    private int textColor(Context c, DiffLine line) {
        if (dark) {
            switch (line.kind) {
                case ADDED: return c.getColor(R.color.diff_dark_add_text);
                case REMOVED: return c.getColor(R.color.diff_dark_del_text);
                case HUNK_HEADER: return c.getColor(R.color.diff_dark_hunk_text);
                default: return c.getColor(R.color.md_dark_on_surface);
            }
        }
        switch (line.kind) {
            case ADDED: return c.getColor(R.color.diff_add_text);
            case REMOVED: return c.getColor(R.color.diff_del_text);
            case HUNK_HEADER: return c.getColor(R.color.diff_hunk_text);
            default: return c.getColor(R.color.md_on_surface);
        }
    }

    static final class Holder extends RecyclerView.ViewHolder {
        final ItemDiffLineBinding binding;

        Holder(ItemDiffLineBinding binding) {
            super(binding.getRoot());
            this.binding = binding;
        }
    }
}