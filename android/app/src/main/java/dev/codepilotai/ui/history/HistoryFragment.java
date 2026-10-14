package dev.codepilotai.ui.history;

import android.os.Bundle;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Toast;
import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.fragment.app.Fragment;
import androidx.lifecycle.ViewModelProvider;
import androidx.navigation.fragment.NavHostFragment;
import androidx.recyclerview.widget.LinearLayoutManager;
import dev.codepilotai.CodePilotApp;
import dev.codepilotai.R;
import dev.codepilotai.data.db.entity.TaskEntity;
import dev.codepilotai.databinding.FragmentHistoryBinding;

/** Past runs: cached list, pull-to-refresh, tap through to the result. */
public final class HistoryFragment extends Fragment implements TaskAdapter.Listener {

    private FragmentHistoryBinding binding;
    private HistoryViewModel viewModel;
    private TaskAdapter adapter;

    @Nullable
    @Override
    public View onCreateView(
            @NonNull LayoutInflater inflater,
            @Nullable ViewGroup container,
            @Nullable Bundle savedInstanceState) {
        binding = FragmentHistoryBinding.inflate(inflater, container, false);
        return binding.getRoot();
    }

    @Override
    public void onViewCreated(@NonNull View view, @Nullable Bundle savedInstanceState) {
        super.onViewCreated(view, savedInstanceState);
        CodePilotApp app = (CodePilotApp) requireActivity().getApplication();
        viewModel =
                new ViewModelProvider(this, app.component().historyViewModelFactory())
                        .get(HistoryViewModel.class);

        adapter = new TaskAdapter(this);
        binding.taskList.setLayoutManager(new LinearLayoutManager(requireContext()));
        binding.taskList.setAdapter(adapter);

        binding.toolbar.setNavigationOnClickListener(v -> requireActivity().onBackPressed());
        binding.refreshLayout.setOnRefreshListener(viewModel::refresh);

        viewModel.taskList().observe(getViewLifecycleOwner(), tasks -> {
            boolean empty = tasks == null || tasks.isEmpty();
            binding.emptyText.setVisibility(empty ? View.VISIBLE : View.GONE);
            adapter.submitList(tasks != null ? tasks : java.util.Collections.emptyList());
        });

        viewModel.refreshing().observe(getViewLifecycleOwner(), refreshing ->
                binding.refreshLayout.setRefreshing(refreshing != null && refreshing));

        viewModel.errors().observe(getViewLifecycleOwner(), message -> {
            if (message != null) {
                Toast.makeText(requireContext(), message, Toast.LENGTH_LONG).show();
            }
        });

        viewModel.opened().observe(getViewLifecycleOwner(), taskId -> {
            if (taskId != null) {
                Bundle args = new Bundle();
                args.putLong("taskId", taskId);
                NavHostFragment.findNavController(this)
                        .navigate(R.id.action_history_to_result, args);
            }
        });

        viewModel.refresh();
    }

    @Override
    public void onTaskClicked(TaskEntity task) {
        viewModel.open(task);
    }

    @Override
    public void onDestroyView() {
        super.onDestroyView();
        binding = null;
    }
}