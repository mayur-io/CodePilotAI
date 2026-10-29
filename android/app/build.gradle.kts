plugins {
    alias(libs.plugins.android.application)
    id("jacoco")
}

// Firebase is opt-in. The google-services plugin fails the build when
// app/google-services.json is absent, and that file is a per-project secret we
// do not check in, so apply it only when it is present. Drop the file in (from
// the Firebase console) to switch push notifications on; until then the app
// builds and runs, and FirebasePush reports "not configured".
if (file("google-services.json").exists()) {
    apply(plugin = "com.google.gms.google-services")
}

android {
    namespace = "dev.codepilotai"
    compileSdk = 36

    defaultConfig {
        applicationId = "dev.codepilotai"
        minSdk = 24
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"

        // Read from local.properties or environment at build time.
        val apiBaseUrl: String = ((project.findProperty("ANDROID_API_BASE_URL") as String?)
            ?: System.getenv("ANDROID_API_BASE_URL")
            ?: "http://10.216.51.215:8000/api/v1").trim()
        buildConfigField("String", "API_BASE_URL", "\"$apiBaseUrl\"")
        buildConfigField("String", "DEEP_LINK_SCHEME", "\"codepilotai\"")
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        viewBinding = true
        buildConfig = true
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

dependencies {
    implementation(libs.androidx.core)
    implementation(libs.androidx.appcompat)
    implementation(libs.material)
    implementation(libs.androidx.constraintlayout)
    implementation(libs.androidx.activity)
    implementation(libs.androidx.fragment)
    implementation(libs.androidx.lifecycle.viewmodel)
    implementation(libs.androidx.lifecycle.livedata)
    implementation(libs.androidx.lifecycle.runtime)
    implementation(libs.androidx.recyclerview)
    implementation(libs.androidx.navigation.fragment)
    implementation(libs.androidx.navigation.ui)
    implementation(libs.androidx.swiperefreshlayout)

    implementation(libs.androidx.room.runtime)
    implementation(libs.androidx.room.rxjava3)
    annotationProcessor(libs.androidx.room.compiler)

    implementation(libs.androidx.security.crypto)
    implementation(libs.androidx.browser)

    implementation(libs.retrofit)
    implementation(libs.retrofit.converter.moshi)
    implementation(libs.retrofit.adapter.rxjava3)
    implementation(libs.okhttp)
    implementation(libs.okhttp.sse)
    implementation(libs.okhttp.logging)
    implementation(libs.moshi)
    implementation(libs.moshi.adapters)

    // Push notifications (FCM). Inert until google-services.json is added.
    implementation(libs.firebase.messaging)

    implementation(libs.rxjava)
    implementation(libs.rxandroid)

    implementation(libs.dagger)
    annotationProcessor(libs.dagger.compiler)

    // WorkManager is wired for the offline task-draft queue (Phase 5).
    implementation(libs.androidx.work.runtime)
    implementation(libs.androidx.work.rxjava3)

    testImplementation(libs.junit)
    testImplementation(libs.truth)
    testImplementation(libs.mockwebserver)
    testImplementation(libs.mockito.core)
}

// Coverage gate (Phase 6): `gradlew jacocoTestReport` → HTML + XML under
// app/build/reports/jacoco/. Counts every pure-JVM unit test against the
// compiled debug classes.
jacoco {
    toolVersion = "0.8.12" // supports Java 17 bytecode
}

tasks.register<JacocoReport>("jacocoTestReport") {
    dependsOn("testDebugUnitTest")

    reports {
        xml.required.set(true)
        html.required.set(true)
        csv.required.set(false)
    }

    val mainClasses = fileTree(
    layout.buildDirectory.dir("intermediates/javac/debug/compileDebugJavaWithJavac/classes")
) {
        exclude(
            "**/R.class",
            "**/R$*.class",
            "**/BuildConfig.class",
            "**/databinding/**",
            "**/BR.class"
        )
    }

    sourceDirectories.setFrom(files("src/main/java"))
    classDirectories.setFrom(mainClasses)
    executionData.setFrom(
        fileTree(layout.buildDirectory)
            .include("jacoco/testDebugUnitTest.exec")
    )
}