package dev.codepilotai.ui.run;

import android.view.LayoutInflater;
import android.view.ViewGroup;
import android.annotation.SuppressLint;
import androidx.annotation.NonNull;
import androidx.recyclerview.widget.RecyclerView;
import dev.codepilotai.R;
import dev.codepilotai.databinding.ItemEventBinding;
import dev.codepilotai.domain.model.RunEvent;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * Streaming activity list on the run screen (phase/log/reasoning/…). Lists are
 * replaced wholesale per event batch, so plain {@code notifyDataSetChanged} is
 * intentional here — DiffUtil would only add bookkeeping.
 */
@SuppressLint("NotifyDataSetChanged")
public final class EventAdapter extends RecyclerView.Adapter<EventAdapter.Holder> {

    private final List<RunEvent> events = new ArrayList<>();

    public void submit(List<RunEvent> events) {
        this.events.clear();
        if (events != null) {
            this.events.addAll(events);
        }
        notifyDataSetChanged();
    }

    @NonNull
    @Override
    public Holder onCreateViewHolder(@NonNull ViewGroup parent, int viewType) {
        ItemEventBinding binding =
                ItemEventBinding.inflate(LayoutInflater.from(parent.getContext()), parent, false);
        return new Holder(binding);
    }

    @Override
    public void onBindViewHolder(@NonNull Holder holder, int position) {
        RunEvent event = events.get(position);
        int labelRes = labelRes(event);
        holder.binding.kindText.setText(
                labelRes == 0
                        ? event.kind
                        : holder.binding.getRoot().getContext().getString(labelRes));
        holder.binding.payloadText.setText(body(event));
    }

    @Override
    public int getItemCount() {
        return events.size();
    }

    /** Layout-time header for the row; {@code 0} means fall back to the raw wire kind. */
    static int labelRes(RunEvent event) {
        RunEvent.Kind kind = event.kindEnum();
        if (kind == null) {
            return 0;
        }
        switch (kind) {
            case STATUS: return R.string.event_status;
            case PHASE: return R.string.event_phase;
            case LOG: return R.string.event_agent;
            case REASONING: return R.string.event_thinking;
            case CHANGE_PROPOSED: return R.string.event_proposed_change;
            case CHANGE_DECIDED: return R.string.event_change_decided;
            case VERIFICATION: return R.string.event_verification;
            case PUSHED: return R.string.event_pushed;
            case MESSAGE: return R.string.event_message;
            case ERROR: return R.string.event_error;
            case DONE: return R.string.event_done;
            default: return 0;
        }
    }

    static String body(RunEvent event) {
        Map<String, Object> p = event.payload;
        if (p == null) {
            return "";
        }
        Object o = p.get("message");
        if (o == null) {
            o = p.get("text");
        }
        if (o == null) {
            o = p.get("status");
        }
        if (o == null) {
            o = p.get("phase");
        }
        if (o == null) {
            o = p.get("path");
        }
        if (o == null) {
            o = p.get("pr_url");
        }
        if (o == null) {
            o = p.get("content");
        }
        if (o == null) {
            o = p.get("note");
        }
        String text = o == null ? p.toString() : String.valueOf(o);
        return text.length() > 320 ? text.substring(0, 320) + "…" : text;
    }

    static final class Holder extends RecyclerView.ViewHolder {
        final ItemEventBinding binding;

        Holder(ItemEventBinding binding) {
            super(binding.getRoot());
            this.binding = binding;
        }
    }
}