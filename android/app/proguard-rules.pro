# Keep Dagger-generated components, Moshi adapters and Room schema holders
# when minification is enabled later.
-keep class dagger.** { *; }
-keepclassmembers class * { @dagger.** <methods>; }
-keep class dev.codepilotai.data.api.dto.** { *; }
-keep class dev.codepilotai.data.db.entity.** { *; }
-keep class androidx.room.** { *; }
-keep @com.squareup.moshi.JsonClass class * { *; }