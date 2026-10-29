package dev.codepilotai.ui.clarify;

import android.annotation.SuppressLint;
import android.text.Editable;
import android.text.TextWatcher;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import androidx.annotation.NonNull;
import androidx.recyclerview.widget.RecyclerView;
import com.google.android.material.chip.Chip;
import dev.codepilotai.databinding.ItemClarificationBinding;
import dev.codepilotai.domain.model.Clarification;
import java.util.ArrayList;
import java.util.List;

/**
 * Renders one card per clarifying question.
 *
 * <p>A choice question shows its options as single-selection chips; a text
 * question (or a choice question that allows a custom answer) also shows a
 * free-text field. Selecting a chip or typing into the field reports the answer
 * back through {@link Listener}, so the fragment can track completeness without
 * the adapter owning any state.
 */
public final class ClarificationAdapter
        extends RecyclerView.Adapter<ClarificationAdapter.Holder> {

    /** Receives the user's answer for one question. */
    public interface Listener {
        void onAnswer(Clarification question, String value);
    }

    private final List<Clarification> questions = new ArrayList<>();
    private final Listener listener;

    public ClarificationAdapter(Listener listener) {
        this.listener = listener;
        setHasStableIds(true);
    }

    @SuppressLint("NotifyDataSetChanged") // The batch is at most three rows.
    public void submit(List<Clarification> items) {
        questions.clear();
        if (items != null) {
            questions.addAll(items);
        }
        notifyDataSetChanged();
    }

    @Override
    public long getItemId(int position) {
        return questions.get(position).id;
    }

    public int getItemCount() {
        return questions.size();
    }

    @NonNull
    @Override
    public Holder onCreateViewHolder(@NonNull ViewGroup parent, int viewType) {
        LayoutInflater inflater = LayoutInflater.from(parent.getContext());
        ItemClarificationBinding binding =
                ItemClarificationBinding.inflate(inflater, parent, false);
        return new Holder(binding);
    }

    @Override
    public void onBindViewHolder(@NonNull Holder holder, int position) {
        holder.bind(questions.get(position), listener);
    }

    static final class Holder extends RecyclerView.ViewHolder {

        private final ItemClarificationBinding binding;
        /** Guards against feedback loops while we set the field programmatically. */
        private boolean bindingText;
        private TextWatcher watcher;

        Holder(ItemClarificationBinding binding) {
            super(binding.getRoot());
            this.binding = binding;
        }

        void bind(Clarification question, Listener listener) {
            binding.questionText.setText(question.question);

            if (question.rationale == null || question.rationale.trim().isEmpty()) {
                binding.rationaleText.setVisibility(View.GONE);
            } else {
                binding.rationaleText.setVisibility(View.VISIBLE);
                binding.rationaleText.setText(question.rationale);
            }

            bindOptions(question, listener);
            bindCustomField(question, listener);
        }

        private void bindOptions(Clarification question, Listener listener) {
            binding.optionGroup.removeAllViews();
            if (!question.isChoice()) {
                binding.optionGroup.setVisibility(View.GONE);
                return;
            }
            binding.optionGroup.setVisibility(View.VISIBLE);
            String current = question.answer;
            for (Clarification.Option option : question.options) {
                Chip chip = new Chip(binding.getRoot().getContext());
                chip.setText(option.label);
                chip.setCheckable(true);
                chip.setEnsureMinTouchTargetSize(false);
                chip.setMinHeight(48);
                chip.setTag(option.value);
                chip.setChecked(option.value != null && option.value.equals(current));
                chip.setOnClickListener(v -> {
                    // Single-selection group: clicking the checked chip clears
                    // it, which is how the user takes an answer back.
                    String value = chip.isChecked() ? option.value : "";
                    if (binding.customInput.getText() != null) {
                        bindingText = true;
                        binding.customInput.setText("");
                        bindingText = false;
                    }
                    listener.onAnswer(question, value);
                });
                binding.optionGroup.addView(chip);
            }
        }

        private void bindCustomField(Clarification question, Listener listener) {
            boolean show = question.isText() || question.allowCustom;
            if (!show) {
                binding.customLayout.setVisibility(View.GONE);
                return;
            }
            binding.customLayout.setVisibility(View.VISIBLE);
            binding.customLayout.setHint(
                    question.isText() ? question.question : binding.getRoot().getContext()
                            .getString(dev.codepilotai.R.string.clarify_custom_hint));

            if (watcher != null) {
                binding.customInput.removeTextChangedListener(watcher);
            }
            bindingText = true;
            binding.customInput.setText(question.isText() && question.answer != null
                    ? question.answer : "");
            bindingText = false;

            watcher = new TextWatcher() {
                @Override
                public void beforeTextChanged(CharSequence s, int start, int count, int after) {
                    // No-op.
                }

                @Override
                public void onTextChanged(CharSequence s, int start, int before, int count) {
                    // No-op.
                }

                @Override
                public void afterTextChanged(Editable s) {
                    if (bindingText) {
                        return;
                    }
                    String text = s == null ? "" : s.toString();
                    if (!text.trim().isEmpty()) {
                        // A typed answer overrides any chosen chip.
                        clearChipSelection();
                    }
                    listener.onAnswer(question, text);
                }
            };
            binding.customInput.addTextChangedListener(watcher);
        }

        private void clearChipSelection() {
            binding.optionGroup.clearCheck();
        }
    }
}
