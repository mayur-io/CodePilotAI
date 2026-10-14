package dev.codepilotai.ui.approval;

import android.content.res.ColorStateList;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.annotation.SuppressLint;
import androidx.annotation.NonNull;
import androidx.recyclerview.widget.RecyclerView;
import com.google.android.material.chip.Chip;
import dev.codepilotai.R;
import dev.codepilotai.domain.model.ChangeStatus;
import dev.codepilotai.domain.model.ProposedChange;
import java.util.ArrayList;
import java.util.List;

/** Horizontal file tabs above the diff; one chip per proposed change. */
/** File-tab chips; selection changes rebind the whole (≤ a few dozen) list. */
@SuppressLint("NotifyDataSetChanged")
public final class FileTabAdapter extends RecyclerView.Adapter<FileTabAdapter.Holder> {

    public interface Listener {
        void onFileSelected(int index);
    }

    private final List<ProposedChange> changes = new ArrayList<>();
    private final Listener listener;
    private int selected = -1;

    public FileTabAdapter(Listener listener) {
        this.listener = listener;
        setHasStableIds(true);
    }

    public void submit(List<ProposedChange> changes) {
        this.changes.clear();
        if (changes != null) {
            this.changes.addAll(changes);
        }
        if (selected >= this.changes.size()) {
            selected = -1;
        }
        notifyDataSetChanged();
    }

    public void select(int index) {
        if (index >= 0 && index < changes.size()) {
            selected = index;
            notifyDataSetChanged();
        }
    }

    public ProposedChange selectedChange() {
        return selected >= 0 && selected < changes.size() ? changes.get(selected) : null;
    }

    @Override
    public long getItemId(int position) {
        return changes.get(position).id;
    }

    @NonNull
    @Override
    public Holder onCreateViewHolder(@NonNull ViewGroup parent, int viewType) {
        Chip chip = new Chip(parent.getContext());
        chip.setCheckable(true);
        chip.setEnsureMinTouchTargetSize(false);
        chip.setMinHeight(48);
        RecyclerView.LayoutParams params =
                new RecyclerView.LayoutParams(
                        ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        params.setMargins(8, 0, 8, 0);
        chip.setLayoutParams(params);
        return new Holder(chip);
    }

    @Override
    public void onBindViewHolder(@NonNull Holder holder, int position) {
        ProposedChange change = changes.get(position);
        Chip chip = holder.chip;
        chip.setText(change.path);
        chip.setChecked(position == selected);
        chip.setChipBackgroundColor(statusColor(chip, change.statusEnum()));
        chip.setOnClickListener(v -> {
            if (listener != null) {
                listener.onFileSelected(position);
            }
        });
        chip.setContentDescription(change.path);
    }

    private ColorStateList statusColor(Chip chip, ChangeStatus status) {
        int color;
        if (status == ChangeStatus.APPROVED || status == ChangeStatus.APPLIED) {
            color = chip.getContext().getColor(R.color.approved_green);
        } else if (status == ChangeStatus.REJECTED || status == ChangeStatus.FAILED) {
            color = chip.getContext().getColor(R.color.rejected_red);
        } else if (status == ChangeStatus.SKIPPED) {
            color = chip.getContext().getColor(R.color.md_outline);
        } else {
            color = chip.getContext().getColor(R.color.chip_blue);
        }
        return ColorStateList.valueOf(color);
    }

    @Override
    public int getItemCount() {
        return changes.size();
    }

    static final class Holder extends RecyclerView.ViewHolder {
        final Chip chip;

        Holder(Chip chip) {
            super(chip);
            this.chip = chip;
        }
    }
}