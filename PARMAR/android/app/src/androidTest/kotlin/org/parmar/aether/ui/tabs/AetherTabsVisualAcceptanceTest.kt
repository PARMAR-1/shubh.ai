package org.parmar.aether.ui.tabs

import android.graphics.Bitmap
import android.os.Build
import androidx.compose.ui.test.assertHasClickAction
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.hasClickAction
import androidx.compose.ui.test.hasScrollAction
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performScrollToIndex
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import java.io.File
import java.io.FileOutputStream
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.parmar.aether.MainActivity
import org.parmar.aether.data.AetherDataLimits
import org.parmar.aether.data.CreateTabResult
import org.parmar.aether.ui.theme.AetherTheme

@RunWith(AndroidJUnit4::class)
class AetherTabsVisualAcceptanceTest {
    @get:Rule
    val composeRule = createAndroidComposeRule<MainActivity>()

    private val screenshots = mutableListOf<ScreenshotEvidence>()

    @Test
    fun realTabsFlowAndFailureFixtureProduceVisualEvidence() {
        var testStatus = "FAIL"
        try {
            composeRule.waitUntil(timeoutMillis = 15_000) {
                composeRule.onAllNodesWithText("Your tabs").fetchSemanticsNodes().isNotEmpty()
            }
            composeRule.onNodeWithText("Your tabs").performClick()
            composeRule.onNodeWithText("A clear space to begin").assertIsDisplayed()
            composeRule.onNodeWithText("LOCAL ONLY").assertIsDisplayed()
            composeRule.onNodeWithContentDescription("Original PARMAR emblem").assertIsDisplayed()
            captureScreenshot("tabs-empty.png", "Actual app, empty local tabs")

            val createButton = composeRule.onNode(hasText("New local tab") and hasClickAction())
            createButton.assertIsEnabled().assertHasClickAction()
            assertMinimumTouchTarget(createButton.fetchSemanticsNode().boundsInRoot.height, "New local tab")
            assertInsideRoot(createButton.fetchSemanticsNode().boundsInRoot.left, createButton.fetchSemanticsNode().boundsInRoot.right)
            createButton.performClick()
            composeRule.onNodeWithText(
                "1 of ${AetherDataLimits.MAX_TABS} local tab descriptors",
            ).assertIsDisplayed()
            composeRule.onNodeWithText("New local tab").assertExists()
            captureScreenshot("tabs-populated.png", "Actual app after creating a local tab")

            repeat(5) {
                composeRule.onNode(hasText("New local tab") and hasClickAction()).performClick()
            }
            val tabsList = composeRule.onNode(hasScrollAction())
            tabsList.performScrollToIndex(5)
            composeRule.onNodeWithText(
                "6 of ${AetherDataLimits.MAX_TABS} local tab descriptors",
            ).assertIsDisplayed()
            captureScreenshot("tabs-scrolled.png", "Actual app with six local tabs, list scrolled to its last item")
            tabsList.performScrollToIndex(0)

            val searchField = composeRule.onNode(hasSetTextAction())
            assertMinimumTouchTarget(searchField.fetchSemanticsNode().boundsInRoot.height, "Search local tabs")
            assertInsideRoot(
                searchField.fetchSemanticsNode().boundsInRoot.left,
                searchField.fetchSemanticsNode().boundsInRoot.right,
            )
            searchField.performTextInput("no matching local tab")
            composeRule.onNodeWithText("No matching tabs").assertIsDisplayed()
            captureScreenshot("tabs-search-empty.png", "Actual app search with no matching local tabs")

            composeRule.setContent {
                AetherTheme {
                    TabsScreen(
                        session = LocalTabsSession(),
                        onCreateTab = { CreateTabResult.Failed("Simulated local storage failure.") },
                        onCloseTab = {},
                        onActivateTab = {},
                        onTogglePin = { _, _ -> },
                        onMoveTab = { _, _ -> },
                        onHome = {},
                    )
                }
            }
            composeRule.onNode(hasText("New local tab") and hasClickAction()).performClick()
            val failureMessage = composeRule.onNodeWithText("Simulated local storage failure.")
            failureMessage.assertIsDisplayed()
            assertEquals(
                "Tab operation failed: Simulated local storage failure.",
                failureMessage.fetchSemanticsNode().config[SemanticsProperties.StateDescription],
            )
            captureScreenshot("tabs-create-failure.png", "Tabs composable with a deterministic create failure fixture")
            testStatus = "PASS"
        } finally {
            writeEvidence(testStatus)
        }
    }

    private fun assertMinimumTouchTarget(heightPx: Float, label: String) {
        val density = composeRule.activity.resources.displayMetrics.density
        assertTrue("$label touch target is below 48dp", heightPx / density >= 48f)
    }

    private fun assertInsideRoot(leftPx: Float, rightPx: Float) {
        val root = composeRule.onRoot().fetchSemanticsNode().boundsInRoot
        assertTrue("The control extends beyond the left edge of the display.", leftPx >= root.left)
        assertTrue("The control extends beyond the right edge of the display.", rightPx <= root.right)
    }

    private fun captureScreenshot(fileName: String, state: String) {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val bitmap = instrumentation.uiAutomation.takeScreenshot()
            ?: throw AssertionError("Android UI automation did not return a screenshot.")
        val target = instrumentation.targetContext
        val directory = File(target.cacheDir, "aether-visual")
        check(directory.mkdirs() || directory.isDirectory) {
            "The screenshot evidence directory could not be created."
        }
        FileOutputStream(File(directory, fileName)).use { output ->
            check(bitmap.compress(Bitmap.CompressFormat.PNG, 100, output)) {
                "The $fileName screenshot could not be encoded."
            }
        }
        val metrics = target.resources.displayMetrics
        screenshots += ScreenshotEvidence(
            fileName = fileName,
            state = state,
            widthPx = bitmap.width,
            heightPx = bitmap.height,
            widthDp = (bitmap.width / metrics.density).toInt(),
            heightDp = (bitmap.height / metrics.density).toInt(),
            densityDpi = metrics.densityDpi,
        )
        bitmap.recycle()
    }

    private fun writeEvidence(testStatus: String) {
        val target = InstrumentationRegistry.getInstrumentation().targetContext
        val directory = File(target.cacheDir, "aether-visual")
        check(directory.mkdirs() || directory.isDirectory) {
            "The screenshot evidence directory could not be created."
        }
        val entries = screenshots.joinToString(",") { screenshot ->
            """{"file":"${jsonEscape(screenshot.fileName)}","state":"${jsonEscape(screenshot.state)}","widthPx":${screenshot.widthPx},"heightPx":${screenshot.heightPx},"widthDp":${screenshot.widthDp},"heightDp":${screenshot.heightDp},"densityDpi":${screenshot.densityDpi}}"""
        }
        File(directory, "evidence.json").writeText(
            """{"testStatus":"$testStatus","deviceModel":"${jsonEscape(Build.MODEL)}","screenshots":[$entries]}""",
        )
    }

    private fun jsonEscape(value: String): String =
        value.replace("\\", "\\\\").replace("\"", "\\\"")

    private data class ScreenshotEvidence(
        val fileName: String,
        val state: String,
        val widthPx: Int,
        val heightPx: Int,
        val widthDp: Int,
        val heightDp: Int,
        val densityDpi: Int,
    )
}
