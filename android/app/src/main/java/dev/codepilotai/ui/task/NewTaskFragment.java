package dev.codepilotai.ui.task;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.os.Bundle;
import android.speech.RecognizerIntent;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.ArrayAdapter;
import android.widget.Toast;
import androidx.activity.result.ActivityResult;
import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.fragment.app.Fragment;
import androidx.lifecycle.ViewModelProvider;
import androidx.navigation.fragment.NavHostFragment;
import dev.codepilotai.CodePilotApp;
import dev.codepilotai.R;
import dev.codepilotai.data.db.entity.RepositoryEntity;
import dev.codepilotai.databinding.FragmentNewTaskBinding;
import dev.codepilotai.util.VoicePrompt;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/** Compose a natural-language task; drafts are queued offline if needed. */
public final class NewTaskFragment extends Fragment {

    private FragmentNewTaskBinding binding;
    private NewTaskViewModel viewModel;
    private final List<RepositoryEntity> repos = new ArrayList<>();
    private ActivityResultLauncher<Intent> voiceLauncher;

    @Override
    public void onCreate(@Nullable Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        // Speech is delegated to the system recognizer activity, which owns its
        // own RECORD_AUDIO permission -- the app never needs to request it.
        voiceLauncher = registerForActivityResult(
                new ActivityResultContracts.StartActivityForResult(), this::onVoiceResult);
    }

    @Nullable
    @Override
    public View onCreateView(
            @NonNull LayoutInflater inflater,
            @Nullable ViewGroup container,
            @Nullable Bundle savedInstanceState) {
        binding = FragmentNewTaskBinding.inflate(inflater, container, false);
        return binding.getRoot();
    }

    @Override
    public void onViewCreated(@NonNull View view, @Nullable Bundle savedInstanceState) {
        super.onViewCreated(view, savedInstanceState);
        CodePilotApp app = (CodePilotApp) requireActivity().getApplication();
        viewModel =
                new ViewModelProvider(this, app.component().newTaskViewModelFactory())
                        .get(NewTaskViewModel.class);

        binding.toolbar.setNavigationOnClickListener(v -> requireActivity().onBackPressed());

        viewModel.repoList().observe(getViewLifecycleOwner(), list -> {
            repos.clear();
            if (list != null) {
                repos.addAll(list);
            }
            bindDropdown();
        });

        viewModel.submitState().observe(getViewLifecycleOwner(), state -> {
            if (state == null) {
                return;
            }
            binding.startButton.setEnabled(state != NewTaskViewModel.SubmitState.SENDING);
            binding.startButton.setText(
                    state == NewTaskViewModel.SubmitState.SENDING
                            ? R.string.loading
                            : R.string.start_task);
        });

        viewModel.createdTaskId().observe(getViewLifecycleOwner(), taskId -> {
            if (taskId != null) {
                Bundle args = new Bundle();
                args.putLong("taskId", taskId);
                NavHostFragment.findNavController(this)
                        .navigate(R.id.action_newTask_to_run, args);
            }
        });

        viewModel.notices().observe(getViewLifecycleOwner(), notice -> {
            if (notice != null) {
                binding.offlineNote.setVisibility(View.VISIBLE);
                binding.offlineNote.setText(notice);
            }
        });

        viewModel.errors().observe(getViewLifecycleOwner(), message -> {
            if (message != null) {
                Toast.makeText(requireContext(), message, Toast.LENGTH_LONG).show();
            }
        });

        binding.startButton.setOnClickListener(v -> submit());

        binding.promptLayout.setEndIconOnClickListener(v -> startVoiceInput());

        viewModel.load();
    }

    private void startVoiceInput() {
        Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
        intent.putExtra(
                RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
        intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.getDefault().toString());
        intent.putExtra(RecognizerIntent.EXTRA_PROMPT, getString(R.string.voice_prompt));
        try {
            voiceLauncher.launch(intent);
        } catch (ActivityNotFoundException e) {
            Toast.makeText(requireContext(), R.string.voice_unavailable, Toast.LENGTH_LONG).show();
        }
    }

    private void onVoiceResult(ActivityResult result) {
        if (result.getResultCode() != Activity.RESULT_OK
                || result.getData() == null
                || binding == null) {
            return;
        }
        ArrayList<String> matches =
                result.getData().getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS);
        if (matches == null || matches.isEmpty()) {
            return;
        }
        String combined = VoicePrompt.append(text(binding.promptInput), matches.get(0));
        binding.promptInput.setText(combined);
        binding.promptInput.setSelection(combined.length());
    }

    private void bindDropdown() {
        List<String> names = new ArrayList<>();
        for (RepositoryEntity repo : repos) {
            names.add(repo.fullName != null ? repo.fullName : repo.name);
        }
        ArrayAdapter<String> adapter =
                new ArrayAdapter<>(requireContext(), android.R.layout.simple_list_item_1, names);
        binding.repoDropdown.setAdapter(adapter);
        if (repos.size() == 1) {
            binding.repoDropdown.setText(names.get(0), false);
            viewModel.selectRepository(repos.get(0).id);
        }
    }

    private void submit() {
        String prompt = text(binding.promptInput);
        String baseBranch = text(binding.baseBranchInput);
        if (prompt.trim().isEmpty()) {
            Toast.makeText(requireContext(), R.string.task_prompt_hint, Toast.LENGTH_LONG).show();
            return;
        }
        int position = binding.repoDropdown.getListSelection() >= 0
                ? binding.repoDropdown.getListSelection()
                : adapterPositionOf(binding.repoDropdown.getText().toString());
        if (position >= 0 && position < repos.size()) {
            viewModel.selectRepository(repos.get(position).id);
        }
        viewModel.submit(prompt, baseBranch.isEmpty() ? null : baseBranch, null);
    }

    private int adapterPositionOf(String text) {
        for (int i = 0; i < repos.size(); i++) {
            String name = repos.get(i).fullName != null ? repos.get(i).fullName : repos.get(i).name;
            if (name.equals(text)) {
                return i;
            }
        }
        return -1;
    }

    private static String text(android.widget.EditText editText) {
        return editText.getText() == null ? "" : editText.getText().toString().trim();
    }

    @Override
    public void onDestroyView() {
        super.onDestroyView();
        binding = null;
    }
}