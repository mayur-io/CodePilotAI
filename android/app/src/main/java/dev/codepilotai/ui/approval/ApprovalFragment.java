package dev.codepilotai.ui.approval;

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
import dev.codepilotai.databinding.DialogFeedbackBinding;
import dev.codepilotai.databinding.FragmentApprovalBinding;
import dev.codepilotai.domain.model.ChangeStatus;
import dev.codepilotai.domain.model.DiffLine;
import dev.codepilotai.domain.model.ProposedChange;
import dev.codepilotai.util.DiffParser;
import java.util.List;

/**
 * Per-change review (Task.md §9): file tabs → collapsible diff hunks → a
 * thumb-reachable approve / reject / request-edit bar, then "apply approved".
 */
public final class ApprovalFragment extends Fragment implements FileTabAdapter.Listener {

    private FragmentApprovalBinding binding;
    private ApprovalViewModel viewModel;
    private FileTabAdapter tabAdapter;
    private DiffLineAdapter diffAdapter;
    private ProposedChange selected;
    private long taskId;

    @Nullable
    @Override
    public View onCreateView(
            @NonNull LayoutInflater inflater,
            @Nullable ViewGroup container,
            @Nullable Bundle savedInstanceState) {
        binding = FragmentApprovalBinding.inflate(inflater, container, false);
        return binding.getRoot();
    }

    @Override
    public void onViewCreated(@NonNull View view, @Nullable Bundle savedInstanceState) {
        super.onViewCreated(view, savedInstanceState);
        CodePilotApp app = (CodePilotApp) requireActivity().getApplication();
        viewModel =
                new ViewModelProvider(this, app.component().approvalViewModelFactory())
                        .get(ApprovalViewModel.class);

        taskId = requireArguments().getLong("taskId");

        boolean dark = (requireContext().getResources().getConfiguration().uiMode
                & android.content.res.Configuration.UI_MODE_NIGHT_MASK)
                == android.content.res.Configuration.UI_MODE_NIGHT_YES;

        tabAdapter = new FileTabAdapter(this);
        binding.fileTabs.setLayoutManager(
                new LinearLayoutManager(requireContext(), LinearLayoutManager.HORIZONTAL, false));
        binding.fileTabs.setAdapter(tabAdapter);

        diffAdapter = new DiffLineAdapter(dark);
        binding.diffList.setLayoutManager(new LinearLayoutManager(requireContext()));
        binding.diffList.setAdapter(diffAdapter);

        binding.toolbar.setNavigationOnClickListener(v -> requireActivity().onBackPressed());

        binding.approveButton.setOnClickListener(v -> decide("approve", null));
        binding.rejectButton.setOnClickListener(v -> decide("reject", null));
        binding.editButton.setOnClickListener(v -> showFeedbackDialog());
        binding.executeButton.setOnClickListener(v -> viewModel.execute());

        viewModel.changes().observe(getViewLifecycleOwner(), this::onChanges);
        viewModel.pendingExists().observe(getViewLifecycleOwner(), pending -> {
            boolean showBar = pending != null && pending;
            binding.decisionBar.setVisibility(showBar ? View.VISIBLE : View.GONE);
        });
        viewModel.canExecute().observe(getViewLifecycleOwner(), can -> {
            boolean show = can != null && can;
            binding.executeButton.setVisibility(show ? View.VISIBLE : View.GONE);
        });
        viewModel.busy().observe(getViewLifecycleOwner(), busy ->
                binding.progress.setVisibility(busy != null && busy ? View.VISIBLE : View.GONE));
        viewModel.errors().observe(getViewLifecycleOwner(), message -> {
            if (message != null) {
                Toast.makeText(requireContext(), message, Toast.LENGTH_LONG).show();
            }
        });
        viewModel.executed().observe(getViewLifecycleOwner(), id -> {
            if (id != null) {
                Bundle args = new Bundle();
                args.putLong("taskId", id);
                NavHostFragment.findNavController(this)
                        .navigate(R.id.action_approval_to_run, args);
            }
        });

        viewModel.start(taskId);
    }

    private void onChanges(List<ProposedChange> changes) {
        tabAdapter.submit(changes);
        if (tabAdapter.selectedChange() == null && !changes.isEmpty()) {
            tabAdapter.select(0);
        }
        onFileSelectedFromAdapter();
    }

    @Override
    public void onFileSelected(int index) {
        tabAdapter.select(index);
        onFileSelectedFromAdapter();
    }

    private void onFileSelectedFromAdapter() {
        selected = tabAdapter.selectedChange();
        if (selected == null) {
            diffAdapter.submit(java.util.Collections.emptyList());
            return;
        }
        List<DiffLine> lines = DiffParser.parse(selected.patch);
        diffAdapter.submit(lines);
    }

    private void decide(String decision, String feedback) {
        if (selected == null || selected.statusEnum() != ChangeStatus.PENDING) {
            Toast.makeText(
                            requireContext(),
                            R.string.no_pending_changes,
                            Toast.LENGTH_SHORT)
                    .show();
            return;
        }
        viewModel.decide(selected.id, decision, feedback);
    }

    private void showFeedbackDialog() {
        if (selected == null || selected.statusEnum() != ChangeStatus.PENDING) {
            Toast.makeText(requireContext(), R.string.no_pending_changes, Toast.LENGTH_SHORT).show();
            return;
        }
        DialogFeedbackBinding dialogBinding =
                DialogFeedbackBinding.inflate(getLayoutInflater());
        com.google.android.material.dialog.MaterialAlertDialogBuilder builder =
                new com.google.android.material.dialog.MaterialAlertDialogBuilder(requireContext())
                        .setView(dialogBinding.getRoot())
                        .setNegativeButton(R.string.cancel, null)
                        .setPositiveButton(R.string.request_edit, (d, which) -> {
                            String feedback = dialogBinding.feedbackText.getText() == null
                                    ? "" : dialogBinding.feedbackText.getText().toString().trim();
                            if (feedback.isEmpty()) {
                                Toast.makeText(requireContext(), R.string.feedback_hint, Toast.LENGTH_SHORT)
                                        .show();
                                return;
                            }
                            decide("request_edit", feedback);
                        });
        builder.show();
    }

    @Override
    public void onDestroyView() {
        super.onDestroyView();
        binding = null;
    }
}