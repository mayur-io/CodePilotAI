package dev.codepilotai.ui.repos;

import android.os.Bundle;
import android.text.Editable;
import android.text.TextWatcher;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Toast;
import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.fragment.app.Fragment;
import androidx.lifecycle.ViewModelProvider;
import androidx.navigation.NavOptions;
import androidx.navigation.fragment.NavHostFragment;
import androidx.recyclerview.widget.LinearLayoutManager;
import com.google.android.material.dialog.MaterialAlertDialogBuilder;
import dev.codepilotai.CodePilotApp;
import dev.codepilotai.R;
import dev.codepilotai.data.db.entity.RepositoryEntity;
import dev.codepilotai.databinding.DialogCreateRepoBinding;
import dev.codepilotai.databinding.FragmentReposBinding;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/** Repository picker: search, sync from GitHub, create, sign out. */
public final class ReposFragment extends Fragment implements RepoAdapter.Listener {

    private FragmentReposBinding binding;
    private ReposViewModel viewModel;
    private RepoAdapter adapter;
    private final List<RepositoryEntity> all = new ArrayList<>();

    @Nullable
    @Override
    public View onCreateView(
            @NonNull LayoutInflater inflater,
            @Nullable ViewGroup container,
            @Nullable Bundle savedInstanceState) {
        binding = FragmentReposBinding.inflate(inflater, container, false);
        return binding.getRoot();
    }

    @Override
    public void onViewCreated(@NonNull View view, @Nullable Bundle savedInstanceState) {
        super.onViewCreated(view, savedInstanceState);
        CodePilotApp app = (CodePilotApp) requireActivity().getApplication();
        viewModel =
                new ViewModelProvider(this, app.component().reposViewModelFactory())
                        .get(ReposViewModel.class);

        adapter = new RepoAdapter(this);
        binding.reposList.setLayoutManager(new LinearLayoutManager(requireContext()));
        binding.reposList.setAdapter(adapter);

        binding.searchInput.addTextChangedListener(new TextWatcher() {
            @Override public void beforeTextChanged(CharSequence s, int a, int b, int c) { }

            @Override public void onTextChanged(CharSequence s, int a, int b, int c) {
                applyFilter(s == null ? "" : s.toString());
            }

            @Override public void afterTextChanged(Editable s) { }
        });

        binding.refreshLayout.setOnRefreshListener(viewModel::load);
        binding.createFab.setOnClickListener(v -> showCreateDialog());
        binding.syncButton.setOnClickListener(v -> viewModel.syncFromGithub());
        binding.historyButton.setOnClickListener(
                v -> NavHostFragment.findNavController(this)
                        .navigate(R.id.action_repos_to_history));
        binding.signOutButton.setOnClickListener(v -> confirmSignOut());

        viewModel.reposList().observe(getViewLifecycleOwner(), repos -> {
            all.clear();
            if (repos != null) {
                all.addAll(repos);
            }
            applyFilter(binding.searchInput.getText() == null
                    ? "" : binding.searchInput.getText().toString());
            binding.emptyText.setVisibility(all.isEmpty() ? View.VISIBLE : View.GONE);
        });

        viewModel.refreshing().observe(getViewLifecycleOwner(), refreshing ->
                binding.refreshLayout.setRefreshing(refreshing != null && refreshing));

        viewModel.errors().observe(getViewLifecycleOwner(), message -> {
            if (message != null) {
                Toast.makeText(requireContext(), message, Toast.LENGTH_LONG).show();
            }
        });

        viewModel.opened().observe(getViewLifecycleOwner(), id -> {
            if (id != null) {
                NavHostFragment.findNavController(this)
                        .navigate(R.id.action_repos_to_newTask);
            }
        });

        viewModel.signedOut().observe(getViewLifecycleOwner(), out -> {
            if (out != null && out) {
                NavOptions options =
                        new NavOptions.Builder().setPopUpTo(R.id.nav_graph, true).build();
                NavHostFragment.findNavController(this).navigate(R.id.authFragment, null, options);
            }
        });

        viewModel.load();
    }

    private void applyFilter(String query) {
        String q = query.trim().toLowerCase(Locale.ROOT);
        List<RepositoryEntity> filtered = new ArrayList<>();
        for (RepositoryEntity repo : all) {
            if (q.isEmpty()
                    || (repo.fullName != null && repo.fullName.toLowerCase(Locale.ROOT).contains(q))
                    || (repo.description != null
                            && repo.description.toLowerCase(Locale.ROOT).contains(q))) {
                filtered.add(repo);
            }
        }
        adapter.submitList(filtered);
    }

    @Override
    public void onRepoClicked(RepositoryEntity repo) {
        viewModel.open(repo);
    }

    private void showCreateDialog() {
        DialogCreateRepoBinding dialogBinding =
                DialogCreateRepoBinding.inflate(getLayoutInflater());
        new MaterialAlertDialogBuilder(requireContext())
                .setView(dialogBinding.getRoot())
                .setNegativeButton(R.string.cancel, null)
                .setPositiveButton(R.string.create, (d, which) -> {
                    String name = dialogBinding.nameInput.getText() == null
                            ? "" : dialogBinding.nameInput.getText().toString().trim();
                    String description = dialogBinding.descriptionInput.getText() == null
                            ? "" : dialogBinding.descriptionInput.getText().toString().trim();
                    if (name.isEmpty()) {
                        Toast.makeText(requireContext(), R.string.repo_name_hint, Toast.LENGTH_SHORT).show();
                        return;
                    }
                    viewModel.create(name, description, dialogBinding.privateSwitch.isChecked());
                })
                .show();
    }

    private void confirmSignOut() {
        new MaterialAlertDialogBuilder(requireContext())
                .setTitle(R.string.sign_out)
                .setMessage(R.string.sign_out)
                .setNegativeButton(R.string.cancel, null)
                .setPositiveButton(R.string.sign_out, (d, which) -> viewModel.signOut())
                .show();
    }

    @Override
    public void onDestroyView() {
        super.onDestroyView();
        binding = null;
    }
}