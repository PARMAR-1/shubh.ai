package org.parmar.aether.ui.bookmarks

import android.content.ClipboardManager
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.parmar.aether.MainActivity
import org.parmar.aether.data.CreateTabResult
import org.parmar.aether.ui.theme.AetherTheme

@RunWith(AndroidJUnit4::class)
class BookmarkCopyTest {
    @get:Rule
    val composeRule = createAndroidComposeRule<MainActivity>()

    @Test
    fun savedAddressCanBeCopiedFromDetailsWithoutOpeningThePage() {
        val url = "https://example.test/guide"
        val bookmark = Bookmark(
            id = "bookmark-1",
            url = url,
            title = "Offline guide",
            createdAt = 1L,
            updatedAt = 1L,
        )
        var savedUrlTabCreationCount = 0
        composeRule.setContent {
            AetherTheme {
                BookmarksScreen(
                    bookmarks = listOf(bookmark),
                    addBookmark = { _, _ -> AddBookmarkResult.InvalidUrl },
                    updateBookmark = { _, _, _ -> AddBookmarkResult.NotFound },
                    onRemoveBookmark = { false },
                    onCreateSavedUrlTab = {
                        savedUrlTabCreationCount += 1
                        CreateTabResult.Failed("Not used in this test.")
                    },
                    onHome = {},
                )
            }
        }

        composeRule.onNodeWithText("View details").performClick()
        composeRule.onNodeWithText("Copy address").performClick()
        composeRule.onNodeWithText("Address copied to clipboard.").assertIsDisplayed()

        val clipboard = checkNotNull(
            composeRule.activity.getSystemService(ClipboardManager::class.java),
        )
        assertEquals(url, clipboard.primaryClip?.getItemAt(0)?.text?.toString())
        assertEquals(0, savedUrlTabCreationCount)
    }
}
