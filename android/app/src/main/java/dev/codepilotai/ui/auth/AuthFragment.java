package dev.codepilotai.ui.auth;

import android.os.Bundle;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.browser.customtabs.CustomTabsIntent;
import androidx.fragment.app.Fragment;
import androidx.lifecycle.ViewModelProvider;
import androidx.navigation.fragment.NavHostFragment;
import dev.codepilotai.CodePilotApp;
import dev.codepilotai.R;
import dev.codepilotai.databinding.FragmentAuthBinding;

/** Sign-in gate: Custom Tab OAuth, then hand off to the repos screen. */
public final class AuthFragment extends Fragment {

    private FragmentAuthBinding binding;
    private AuthViewModel viewModel;

    @Nullable
    @Override
    public View onCreateView(
            @NonNull LayoutInflater inflater,
            @Nullable ViewGroup container,
            @Nullable Bundle savedInstanceState) {
        binding = FragmentAuthBinding.inflate(inflater, container, false);
        return binding.getRoot();
    }

    @Override
    public void onViewCreated(@NonNull View view, @Nullable Bundle savedInstanceState) {
        super.onViewCreated(view, savedInstanceState);
        CodePilotApp app = (CodePilotApp) requireActivity().getApplication();
        // Activity-scoped: MainActivity exchanges deep links into this instance.
        viewModel =
                new ViewModelProvider(requireActivity(), app.component().authViewModelFactory())
                        .get(AuthViewModel.class);

        binding.githubButton.setOnClickListener(v -> {
            binding.githubButton.setEnabled(false);
            viewModel.beginSignIn();
        });

        viewModel.authorizeUrl().observe(getViewLifecycleOwner(), url -> {
            binding.githubButton.setEnabled(true);
            if (url == null || url.trim().isEmpty()) {
                return;
            }
            CustomTabsIntent intent =
                    new CustomTabsIntent.Builder().setShowTitle(true).build();
            intent.launchUrl(requireContext(), android.net.Uri.parse(url));
        });

        viewModel.signedIn().observe(getViewLifecycleOwner(), signedIn -> {
            if (signedIn != null && signedIn) {
                NavHostFragment.findNavController(this)
                        .navigate(R.id.action_auth_to_repos);
            }
        });

        viewModel.signInFailed().observe(getViewLifecycleOwner(), message -> {
            binding.progress.setVisibility(View.GONE);
            binding.statusText.setVisibility(View.VISIBLE);
            binding.statusText.setText(getString(R.string.sign_in_failed, message));
        });

        viewModel.state().observe(getViewLifecycleOwner(), state -> {
            if (state == null) {
                return;
            }
            binding.progress.setVisibility(state.checking ? View.VISIBLE : View.GONE);
            if (state.checking) {
                binding.statusText.setVisibility(View.VISIBLE);
                binding.statusText.setText(R.string.signing_in);
            } else if (state.error != null) {
                binding.statusText.setVisibility(View.VISIBLE);
                binding.statusText.setText(getString(R.string.sign_in_failed, state.error));
            } else {
                binding.statusText.setVisibility(View.GONE);
            }
        });
    }

    @Override
    public void onResume() {
        super.onResume();
        if (viewModel != null) {
            viewModel.checkExistingSession();
            if (viewModel.isSignedIn()) {
                NavHostFragment.findNavController(this)
                        .navigate(R.id.action_auth_to_repos);
            }
        }
    }

    @Override
    public void onDestroyView() {
        super.onDestroyView();
        binding = null;
    }
}