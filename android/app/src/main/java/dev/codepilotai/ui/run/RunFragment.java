package dev.codepilotai.ui.run;

import android.os.Bundle;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.fragment.app.Fragment;
import androidx.lifecycle.ViewModelProvider;
import androidx.navigation.fragment.NavHostFragment;
import androidx.recyclerview.widget.LinearLayoutManager;
import dev.codepilotai.CodePilotApp;
import dev.codepilotai.R;
import dev.codepilotai.databinding.FragmentRunBinding;
import dev.codepilotai.domain.model.RunEvent;
import java.util.ArrayList;
import java.util.List;

/** Streaming run: phase header, expandable reasoning, live activity feed. */
public final class RunFragment extends Fragment {

    private FragmentRunBinding binding;
    private RunViewModel viewModel;
    private EventAdapter adapter;
    private boolean showReasoning;

    @Nullable
    @Override
    public View onCreateView(
            @NonNull LayoutInflater inflater,
            @Nullable ViewGroup container,
            @Nullable Bundle savedInstanceState) {
        binding = FragmentRunBinding.inflate(inflater, container, false);
        return binding.getRoot();
    }

    @Override
    public void onViewCreated(@NonNull View view, @Nullable Bundle savedInstanceState) {
        super.onViewCreated(view, savedInstanceState);
        CodePilotApp app = (CodePilotApp) requireActivity().getApplication();
        viewModel =
                new ViewModelProvider(this, app.component().runViewModelFactory())
                        .get(RunViewModel.class);

        long taskId = requireArguments().getLong("taskId");

        adapter = new EventAdapter();
        binding.eventList.setLayoutManager(new LinearLayoutManager(requireContext()));
        binding.eventList.setAdapter(adapter);

        binding.toolbar.setNavigationOnClickListener(v -> requireActivity().onBackPressed());
        binding.reasoningToggle.setOnClickListener(v -> toggleReasoning());

        viewModel.events().observe(getViewLifecycleOwner(), events -> {
            if (events == null) {
                return;
            }
            List<RunEvent> filtered = new ArrayList<>();
            if (showReasoning) {
                filtered.addAll(events);
            } else {
                for (RunEvent e : events) {
                    if (e.kindEnum() != RunEvent.Kind.REASONING) {
                        filtered.add(e);
                    }
                }
            }
            adapter.submit(filtered);
            if (!filtered.isEmpty()) {
                binding.eventList.scrollToPosition(filtered.size() - 1);
            }
            binding.emptyText.setVisibility(
                    filtered.isEmpty() ? View.VISIBLE : View.GONE);
        });

        viewModel.reasoningCount().observe(getViewLifecycleOwner(), count -> {
            int value = count == null ? 0 : count;
            String label =
                    getString(showReasoning ? R.string.hide_reasoning : R.string.show_reasoning);
            binding.reasoningToggle.setText(
                    value > 0 ? getString(R.string.reasoning_toggle_count, label, value) : label);
            binding.reasoningToggle.setVisibility(value > 0 ? View.VISIBLE : View.INVISIBLE);
        });

        viewModel.phaseRes().observe(getViewLifecycleOwner(), phaseRes -> {
            if (phaseRes != null) {
                binding.phaseText.setText(getString(phaseRes));
            }
        });

        viewModel.connected().observe(getViewLifecycleOwner(), connected -> {
            boolean ok = connected != null && connected;
            binding.connectionDot.setBackgroundResource(
                    ok ? R.drawable.dot_connected : R.drawable.dot_disconnected);
        });

        viewModel.approvalRequired().observe(getViewLifecycleOwner(), id -> {
            if (id != null) {
                Bundle args = new Bundle();
                args.putLong("taskId", id);
                NavHostFragment.findNavController(this)
                        .navigate(R.id.action_run_to_approval, args);
            }
        });

        viewModel.terminalReached().observe(getViewLifecycleOwner(), status -> {
            if (status != null) {
                Bundle args = new Bundle();
                args.putLong("taskId", taskId);
                NavHostFragment.findNavController(this)
                        .navigate(R.id.action_run_to_result, args);
            }
        });

        viewModel.start(taskId);
    }

    private void toggleReasoning() {
        showReasoning = !showReasoning;
        List<RunEvent> all = viewModel.events().getValue();
        if (all == null) {
            return;
        }
        List<RunEvent> filtered = new ArrayList<>();
        if (showReasoning) {
            filtered.addAll(all);
        } else {
            for (RunEvent e : all) {
                if (e.kindEnum() != RunEvent.Kind.REASONING) {
                    filtered.add(e);
                }
            }
        }
        adapter.submit(filtered);
        Integer count = viewModel.reasoningCount().getValue();
        String label =
                getString(showReasoning ? R.string.hide_reasoning : R.string.show_reasoning);
        binding.reasoningToggle.setText(
                count != null && count > 0
                        ? getString(R.string.reasoning_toggle_count, label, count)
                        : label);
    }

    @Override
    public void onDestroyView() {
        super.onDestroyView();
        binding = null;
    }
}