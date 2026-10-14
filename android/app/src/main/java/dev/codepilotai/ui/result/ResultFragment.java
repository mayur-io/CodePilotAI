package dev.codepilotai.ui.result;

import android.os.Bundle;
import android.text.TextUtils;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Toast;
import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.fragment.app.Fragment;
import androidx.lifecycle.ViewModelProvider;
import dev.codepilotai.CodePilotApp;
import dev.codepilotai.R;
import dev.codepilotai.databinding.FragmentResultBinding;
import dev.codepilotai.domain.model.AgentTaskDetail;
import dev.codepilotai.domain.model.RunEvent;
import dev.codepilotai.domain.model.TaskStatus;
import dev.codepilotai.domain.model.VerificationResult;
import dev.codepilotai.ui.MainActivity;
import dev.codepilotai.util.AuditLog;
import dev.codepilotai.util.Labels;
import java.util.List;
import java.util.Locale;

/** Final screen: summary, verification cards, PR link, token/cost stats. */
public final class ResultFragment extends Fragment {

    private FragmentResultBinding binding;
    private ResultViewModel viewModel;
    private boolean auditExpanded;

    @Nullable
    @Override
    public View onCreateView(
            @NonNull LayoutInflater inflater,
            @Nullable ViewGroup container,
            @Nullable Bundle savedInstanceState) {
        binding = FragmentResultBinding.inflate(inflater, container, false);
        return binding.getRoot();
    }

    @Override
    public void onViewCreated(@NonNull View view, @Nullable Bundle savedInstanceState) {
        super.onViewCreated(view, savedInstanceState);
        CodePilotApp app = (CodePilotApp) requireActivity().getApplication();
        viewModel =
                new ViewModelProvider(this, app.component().resultViewModelFactory())
                        .get(ResultViewModel.class);

        long taskId = requireArguments().getLong("taskId");
        binding.toolbar.setNavigationOnClickListener(v -> requireActivity().onBackPressed());

        binding.prButton.setOnClickListener(v -> {
            ResultViewModel.State s = viewModel.state().getValue();
            if (s != null && s.detail != null) {
                viewModel.openPullRequest(s.detail);
            }
        });
        binding.retryButton.setOnClickListener(v -> viewModel.load(taskId));
        binding.auditToggle.setOnClickListener(v -> toggleAudit());

        viewModel.state().observe(getViewLifecycleOwner(), state -> {
            if (state == null) {
                return;
            }
            binding.progress.setVisibility(state.loading ? View.VISIBLE : View.GONE);
            if (state.detail != null) {
                render(state.detail);
            } else if (state.error != null) {
                binding.errorText.setVisibility(View.VISIBLE);
                binding.errorText.setText(state.error);
                binding.retryButton.setVisibility(View.VISIBLE);
            }
        });

        viewModel.openUrl().observe(getViewLifecycleOwner(), url -> {
            if (url != null && getActivity() instanceof MainActivity) {
                MainActivity.openUrl((MainActivity) getActivity(), url);
            }
        });

        viewModel.auditEvents().observe(getViewLifecycleOwner(), this::renderAudit);

        viewModel.load(taskId);
    }

    private void renderAudit(List<RunEvent> events) {
        if (events == null || events.isEmpty()) {
            binding.auditToggle.setVisibility(View.GONE);
            binding.auditCard.setVisibility(View.GONE);
            return;
        }
        StringBuilder sb = new StringBuilder();
        for (RunEvent event : events) {
            sb.append(AuditLog.timestamp(event.createdAt))
                    .append("  ")
                    .append(AuditLog.render(AuditLog.summary(event), this::resolve))
                    .append('\n');
        }
        binding.auditText.setText(sb.toString().trim());
        binding.auditToggle.setVisibility(View.VISIBLE);
        binding.auditCard.setVisibility(auditExpanded ? View.VISIBLE : View.GONE);
    }

    private void toggleAudit() {
        auditExpanded = !auditExpanded;
        binding.auditCard.setVisibility(auditExpanded ? View.VISIBLE : View.GONE);
        binding.auditToggle.setText(
                auditExpanded ? R.string.hide_activity_log : R.string.show_activity_log);
    }

    private void render(AgentTaskDetail detail) {
        TaskStatus status = detail.statusEnum();
        String statusLabel = status == null ? detail.status : getString(Labels.statusLabelRes(status));
        binding.statusText.setText(statusLabel);

        if (detail.errorMessage != null && !detail.errorMessage.trim().isEmpty()) {
            binding.errorText.setVisibility(View.VISIBLE);
            binding.errorText.setText(getString(R.string.task_failed, detail.errorMessage));
        } else if (detail.agentSummary != null && !detail.agentSummary.trim().isEmpty()) {
            binding.agentSummary.setText(detail.agentSummary);
        } else {
            binding.agentSummary.setText(detail.prompt);
        }

        if (detail.diffStat != null && !detail.diffStat.trim().isEmpty()) {
            binding.diffStat.setText(detail.diffStat);
            binding.diffStat.setVisibility(View.VISIBLE);
        }

        if (detail.prUrl != null && !detail.prUrl.trim().isEmpty()) {
            binding.prButton.setVisibility(View.VISIBLE);
            if (detail.prNumber != null && detail.prNumber > 0) {
                binding.prButton.setText(getString(R.string.pr_created, detail.prNumber));
            }
        }

        VerificationResult verification = detail.verification;
        if (verification != null && !verification.outcomes.isEmpty()) {
            binding.verificationCard.setVisibility(View.VISIBLE);
            StringBuilder sb = new StringBuilder();
            for (VerificationResult.Outcome o : verification.outcomes) {
                sb.append(o.passed ? "✓ " : "✗ ")
                        .append(o.command)
                        .append("\n");
                if (o.output != null && !o.output.trim().isEmpty()) {
                    sb.append(truncate(o.output, 280)).append("\n");
                }
            }
            if (verification.skipped) {
                sb.insert(0, "no checks detected\n");
            }
            binding.verificationText.setText(sb.toString().trim());
        }

        long tokens = detail.totalTokens();
        binding.tokensText.setText(
                getResources().getQuantityString(R.plurals.tokens_used, (int) tokens, tokens));
        if (detail.cachedTokens > 0) {
            String cached =
                    getResources()
                            .getQuantityString(
                                    R.plurals.tokens_cached,
                                    (int) detail.cachedTokens,
                                    detail.cachedTokens);
            binding.tokensText.append("\n" + cached);
        }
        if (detail.costUsd != null && !detail.costUsd.trim().isEmpty()) {
            binding.costText.setText(getString(R.string.cost_usd, detail.costUsd));
        }
    }

    private static String truncate(String s, int max) {
        if (s.length() <= max) {
            return s;
        }
        return s.substring(0, max) + "…";
    }

    /** Resolves an audit line's resource id (and nested args) against this screen's locale. */
    private String resolve(int resId, Object[] args) {
        return requireContext().getString(resId, args);
    }

    @Override
    public void onDestroyView() {
        super.onDestroyView();
        binding = null;
    }
}