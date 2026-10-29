package dev.codepilotai.ui.clarify;

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
import dev.codepilotai.databinding.FragmentClarifyBinding;
import dev.codepilotai.domain.model.Clarification;
import java.util.List;

/**
 * Clarify screen: the agent asks the questions it needs answered before it
 * writes any code, so an underspecified task ("make a login form") does not turn
 * into guessed-at work. The user taps options (or types a custom answer) and
 * submits the whole batch, which resumes the run.
 */
public final class ClarifyFragment extends Fragment implements ClarificationAdapter.Listener {

    private FragmentClarifyBinding binding;
    private ClarifyViewModel viewModel;
    private ClarificationAdapter adapter;
    private long taskId;

    @Nullable
    @Override
    public View onCreateView(
            @NonNull LayoutInflater inflater,
            @Nullable ViewGroup container,
            @Nullable Bundle savedInstanceState) {
        binding = FragmentClarifyBinding.inflate(inflater, container, false);
        return binding.getRoot();
    }

    @Override
    public void onViewCreated(@NonNull View view, @Nullable Bundle savedInstanceState) {
        super.onViewCreated(view, savedInstanceState);
        CodePilotApp app = (CodePilotApp) requireActivity().getApplication();
        viewModel =
                new ViewModelProvider(this, app.component().clarifyViewModelFactory())
                        .get(ClarifyViewModel.class);

        taskId = requireArguments().getLong("taskId");

        adapter = new ClarificationAdapter(this);
        binding.questionList.setLayoutManager(new LinearLayoutManager(requireContext()));
        binding.questionList.setAdapter(adapter);

        binding.toolbar.setNavigationOnClickListener(v -> requireActivity().onBackPressed());
        binding.submitButton.setOnClickListener(v -> viewModel.submit());

        viewModel.questions().observe(getViewLifecycleOwner(), this::onQuestions);
        viewModel.loading().observe(getViewLifecycleOwner(), loading -> {
            boolean show = loading != null && loading;
            binding.progress.setVisibility(show ? View.VISIBLE : View.GONE);
        });
        viewModel.busy().observe(getViewLifecycleOwner(), busy -> {
            boolean show = busy != null && busy;
            binding.progress.setVisibility(show ? View.VISIBLE : View.GONE);
            binding.submitButton.setEnabled(!show);
        });
        viewModel.allAnswered().observe(getViewLifecycleOwner(), all -> {
            boolean ready = all != null && all;
            binding.submitButton.setEnabled(ready);
        });
        viewModel.errors().observe(getViewLifecycleOwner(), message -> {
            if (message != null) {
                Toast.makeText(requireContext(), message, Toast.LENGTH_LONG).show();
            }
        });
        viewModel.submitted().observe(getViewLifecycleOwner(), id -> {
            if (id != null) {
                Toast.makeText(requireContext(), R.string.clarify_sent, Toast.LENGTH_SHORT).show();
                Bundle args = new Bundle();
                args.putLong("taskId", id);
                NavHostFragment.findNavController(this)
                        .navigate(R.id.action_clarify_to_run, args);
            }
        });

        viewModel.start(taskId);
    }

    private void onQuestions(List<Clarification> questions) {
        adapter.submit(questions);
        boolean empty = questions == null || questions.isEmpty();
        binding.questionList.setVisibility(empty ? View.GONE : View.VISIBLE);
        binding.intro.setVisibility(empty ? View.GONE : View.VISIBLE);
        binding.emptyText.setVisibility(empty ? View.VISIBLE : View.GONE);
        binding.submitButton.setVisibility(empty ? View.GONE : View.VISIBLE);
    }

    @Override
    public void onAnswer(Clarification question, String value) {
        viewModel.setAnswer(question, value);
    }

    @Override
    public void onDestroyView() {
        super.onDestroyView();
        binding = null;
    }
}
