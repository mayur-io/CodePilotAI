// Top-level build file. Plugin versions live in gradle/libs.versions.toml.
plugins {
    alias(libs.plugins.android.application) apply false
    // Declared here so the app module can apply it conditionally, only when
    // app/google-services.json exists (that file is a per-project secret).
    alias(libs.plugins.google.services) apply false
}