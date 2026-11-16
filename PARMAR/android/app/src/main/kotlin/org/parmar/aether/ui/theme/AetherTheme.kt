package org.parmar.aether.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

object AetherColors {
    val canvas = Color(0xFF0C1117)
    val surface = Color(0xFF141B22)
    val surfaceRaised = Color(0xFF1B242D)
    val textPrimary = Color(0xFFF2F3F4)
    val textSecondary = Color(0xFFB7C0C8)
    val accentCopper = Color(0xFFD5A36C)
    val accentSilver = Color(0xFFCAD2D9)
    val statusRestricted = Color(0xFFFFB4AB)
    val borderSubtle = Color(0xFF394550)
}

object AetherSpacing {
    const val small = 8
    const val medium = 16
    const val large = 24
}

private val aetherColorScheme = darkColorScheme(
    primary = AetherColors.accentCopper,
    onPrimary = AetherColors.canvas,
    secondary = AetherColors.accentCopper,
    onSecondary = AetherColors.canvas,
    background = AetherColors.canvas,
    onBackground = AetherColors.textPrimary,
    surface = AetherColors.surface,
    onSurface = AetherColors.textPrimary,
    outline = AetherColors.borderSubtle,
    error = AetherColors.statusRestricted,
    onError = AetherColors.canvas,
)

@Composable
fun AetherTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = aetherColorScheme,
        typography = Typography(),
        content = content,
    )
}
