package org.parmar.aether.ui.tabs

import androidx.compose.foundation.Image
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.Composable
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.ui.Alignment
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import org.parmar.aether.data.AetherDataLimits
import org.parmar.aether.data.CreateTabResult
import org.parmar.aether.ui.CanonicalEmblem
import org.parmar.aether.ui.theme.AetherColors
import org.parmar.aether.ui.theme.AetherSpacing
import kotlinx.coroutines.launch

@Composable
fun TabsScreen(
    session: LocalTabsSession,
    onCreateTab: suspend () -> CreateTabResult,
    onCloseTab: suspend (String) -> Unit,
    onActivateTab: suspend (String) -> Unit,
    onTogglePin: suspend (String, Boolean) -> Unit,
    onMoveTab: suspend (String, Int) -> Unit,
    onHome: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    val emblem = remember(context) { CanonicalEmblem.load(context) }
    val focusManager = LocalFocusManager.current
    var pendingClose by remember { mutableStateOf<LocalTab?>(null) }
    var message by remember { mutableStateOf<String?>(null) }
    var search by remember { mutableStateOf("") }
    var creatingTab by remember { mutableStateOf(false) }
    val visibleTabs = session.tabs.filter { it.matchesSearch(search) }
    BoxWithConstraints(
        modifier = Modifier.fillMaxSize(),
    ) {
        Column(
            modifier = Modifier
                .widthIn(max = 920.dp)
                .fillMaxSize()
                .align(Alignment.TopCenter)
                .padding(horizontal = if (maxWidth < 400.dp) 16.dp else AetherSpacing.large.dp)
                .padding(vertical = 20.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Row(
                    modifier = Modifier.weight(1f),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    Image(
                        bitmap = emblem,
                        contentDescription = "Original PARMAR emblem",
                        modifier = Modifier.size(40.dp),
                        contentScale = ContentScale.Fit,
                    )
                    Column {
                        Text(
                            text = "AETHER",
                            style = MaterialTheme.typography.labelMedium,
                            color = AetherColors.accentCopper,
                            fontWeight = FontWeight.SemiBold,
                        )
                        Text(
                            text = "Your tabs",
                            modifier = Modifier.semantics { heading() },
                            style = MaterialTheme.typography.headlineSmall,
                            color = MaterialTheme.colorScheme.onBackground,
                            fontWeight = FontWeight.SemiBold,
                        )
                    }
                }
                OutlinedButton(
                    onClick = onHome,
                    modifier = Modifier.heightIn(min = 48.dp),
                ) {
                    Text("Home")
                }
            }

            Text(
                text = "${session.tabs.size} ${if (session.tabs.size == 1) "tab" else "tabs"} · ${AetherDataLimits.MAX_TABS} maximum",
                modifier = Modifier.padding(start = 52.dp, top = 2.dp),
                style = MaterialTheme.typography.bodySmall,
                color = AetherColors.textSecondary,
            )

            Card(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 18.dp)
                    .border(
                        width = 1.dp,
                        color = AetherColors.borderSubtle,
                        shape = RoundedCornerShape(18.dp),
                    ),
                shape = RoundedCornerShape(18.dp),
                colors = CardDefaults.cardColors(containerColor = AetherColors.surface),
            ) {
                Column(
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 14.dp),
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    Text(
                        text = "LOCAL ONLY",
                        style = MaterialTheme.typography.labelSmall,
                        color = AetherColors.accentCopper,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Text(
                        text = "Offline shell · remote browsing is disabled.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurface,
                    )
                    Text(
                        text = "Saved addresses remain inert; titles and URLs are searchable local metadata only.",
                        style = MaterialTheme.typography.bodySmall,
                        color = AetherColors.textSecondary,
                    )
                }
            }

            Button(
                onClick = {
                    scope.launch {
                        creatingTab = true
                        message = null
                        try {
                            message = when (val result = onCreateTab()) {
                                is CreateTabResult.Created -> null
                                CreateTabResult.LimitReached -> "The maximum of ${AetherDataLimits.MAX_TABS} tabs has been reached."
                                is CreateTabResult.Failed -> result.message
                            }
                        } finally {
                            creatingTab = false
                        }
                    }
                },
                enabled = !creatingTab && session.tabs.size < AetherDataLimits.MAX_TABS,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 14.dp)
                    .heightIn(min = 54.dp),
            ) {
                if (creatingTab) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(18.dp),
                        strokeWidth = 2.dp,
                    )
                    Spacer(modifier = Modifier.width(10.dp))
                    Text("Creating local tab…")
                } else {
                    Text("New local tab")
                }
            }
            LinearProgressIndicator(
                progress = { session.tabs.size.toFloat() / AetherDataLimits.MAX_TABS },
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 12.dp)
                    .height(4.dp),
                color = AetherColors.accentCopper,
                trackColor = AetherColors.surfaceRaised,
            )
            Text(
                text = message ?: "${session.tabs.size} of ${AetherDataLimits.MAX_TABS} local tab descriptors",
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 6.dp)
                    .semantics {
                        if (message != null) stateDescription = "Tab operation failed: ${message.orEmpty()}"
                    },
                style = MaterialTheme.typography.bodySmall,
                color = if (message != null) MaterialTheme.colorScheme.error else AetherColors.textSecondary,
            )

            if (session.tabs.isEmpty()) {
                EmptyTabsState(modifier = Modifier.weight(1f).padding(top = 16.dp))
            } else {
                OutlinedTextField(
                    value = search,
                    onValueChange = { search = it },
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(top = 14.dp),
                    label = { Text("Search local tabs") },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                    keyboardActions = KeyboardActions(onSearch = { focusManager.clearFocus() }),
                    trailingIcon = {
                        if (search.isNotEmpty()) {
                            TextButton(
                                onClick = { search = "" },
                                modifier = Modifier.heightIn(min = 48.dp),
                            ) {
                                Text("Clear")
                            }
                        }
                    },
                )
                Spacer(modifier = Modifier.height(14.dp))
                if (visibleTabs.isEmpty()) {
                    NoMatchingTabs(modifier = Modifier.weight(1f))
                } else {
                    LazyColumn(
                        modifier = Modifier.weight(1f),
                        verticalArrangement = Arrangement.spacedBy(12.dp),
                        contentPadding = androidx.compose.foundation.layout.PaddingValues(bottom = 12.dp),
                    ) {
                        items(
                            items = visibleTabs,
                            key = LocalTab::id,
                        ) { tab ->
                            LocalTabCard(
                                tab = tab,
                                isActive = session.activeTabId == tab.id,
                                canMoveUp = session.tabs.indexOf(tab) > 0 &&
                                    session.tabs[session.tabs.indexOf(tab) - 1].pinned == tab.pinned,
                                canMoveDown = session.tabs.indexOf(tab) < session.tabs.lastIndex &&
                                    session.tabs[session.tabs.indexOf(tab) + 1].pinned == tab.pinned,
                                onSelect = { scope.launch { onActivateTab(tab.id) } },
                                onPin = { scope.launch { onTogglePin(tab.id, !tab.pinned) } },
                                onMove = { direction -> scope.launch { onMoveTab(tab.id, direction) } },
                                onClose = { pendingClose = tab },
                            )
                        }
                    }
                }
            }
        }
    }

    pendingClose?.let { tab ->
        AlertDialog(
            onDismissRequest = { pendingClose = null },
            title = { Text("Close local tab?") },
            text = { Text("This removes the local descriptor and updates the active tab. The saved address is not opened.") },
            confirmButton = {
                TextButton(onClick = {
                    scope.launch {
                        onCloseTab(tab.id)
                        pendingClose = null
                    }
                }) {
                    Text("Close tab")
                }
            },
            dismissButton = {
                TextButton(onClick = { pendingClose = null }) {
                    Text("Cancel")
                }
            },
        )
    }
}

@Composable
private fun EmptyTabsState(modifier: Modifier = Modifier) {
    Card(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(18.dp),
        colors = CardDefaults.cardColors(containerColor = AetherColors.surface),
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            Text(
                text = "AETHER · LOCAL SESSION",
                style = MaterialTheme.typography.labelSmall,
                color = AetherColors.accentSilver,
                fontWeight = FontWeight.SemiBold,
            )
            Text(
                text = "A clear space to begin",
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 10.dp)
                    .semantics { heading() },
                style = MaterialTheme.typography.titleLarge,
                color = MaterialTheme.colorScheme.onSurface,
                textAlign = TextAlign.Center,
            )
            Text(
                text = "Create a local tab descriptor. It will not load a website or start browsing.",
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 8.dp),
                style = MaterialTheme.typography.bodyMedium,
                color = AetherColors.textSecondary,
                textAlign = TextAlign.Center,
            )
        }
    }
}

@Composable
private fun NoMatchingTabs(
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .padding(top = 24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(
            text = "No matching tabs",
            modifier = Modifier.semantics { heading() },
            style = MaterialTheme.typography.titleMedium,
            color = MaterialTheme.colorScheme.onBackground,
        )
        Text(
            text = "Try another title or saved address.",
            color = AetherColors.textSecondary,
            textAlign = TextAlign.Center,
        )
    }
}

@Composable
private fun LocalTabCard(
    tab: LocalTab,
    isActive: Boolean,
    canMoveUp: Boolean,
    canMoveDown: Boolean,
    onSelect: () -> Unit,
    onPin: () -> Unit,
    onMove: (Int) -> Unit,
    onClose: () -> Unit,
) {
    val cardShape = RoundedCornerShape(18.dp)
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .border(1.dp, AetherColors.borderSubtle, cardShape),
        shape = cardShape,
        colors = CardDefaults.cardColors(containerColor = AetherColors.surfaceRaised),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = if (tab.title.isNullOrBlank()) {
                            if (tab.kind == LocalTabKind.NEW) "New local tab" else "Saved URL tab"
                        } else {
                            tab.title
                        },
                        style = MaterialTheme.typography.titleMedium,
                        color = MaterialTheme.colorScheme.onSurface,
                        fontWeight = FontWeight.SemiBold,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                    Text(
                        text = if (tab.kind == LocalTabKind.SAVED_URL) {
                            "Saved address · metadata only"
                        } else {
                            "Local descriptor · no website loaded"
                        },
                        modifier = Modifier.padding(top = 4.dp),
                        style = MaterialTheme.typography.bodySmall,
                        color = AetherColors.textSecondary,
                    )
                }
                if (isActive) {
                    Text(
                        text = "ACTIVE",
                        modifier = Modifier
                            .padding(start = 12.dp)
                            .semantics { stateDescription = "Active local tab" },
                        style = MaterialTheme.typography.labelSmall,
                        color = AetherColors.accentCopper,
                        fontWeight = FontWeight.SemiBold,
                    )
                }
            }
            if (tab.kind == LocalTabKind.SAVED_URL && !tab.url.isNullOrBlank()) {
                Text(
                    text = tab.url,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                    style = MaterialTheme.typography.bodySmall,
                    color = AetherColors.accentSilver,
                )
            }
            if (isActive) {
                TextButton(
                    onClick = onPin,
                    modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
                ) {
                    Text(if (tab.pinned) "Unpin tab" else "Pin tab")
                }
            } else {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    OutlinedButton(
                        onClick = onSelect,
                        modifier = Modifier.weight(1f).heightIn(min = 48.dp),
                    ) {
                        Text("Select tab")
                    }
                    TextButton(
                        onClick = onPin,
                        modifier = Modifier.weight(1f).heightIn(min = 48.dp),
                    ) {
                        Text(if (tab.pinned) "Unpin tab" else "Pin tab")
                    }
                }
            }
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                TextButton(
                    onClick = { onMove(-1) },
                    enabled = canMoveUp,
                    modifier = Modifier.weight(1f).heightIn(min = 48.dp),
                ) {
                    Text("↑ Move up", maxLines = 1)
                }
                TextButton(
                    onClick = { onMove(1) },
                    enabled = canMoveDown,
                    modifier = Modifier.weight(1f).heightIn(min = 48.dp),
                ) {
                    Text("↓ Move down", maxLines = 1)
                }
            }
            TextButton(
                onClick = onClose,
                modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
                colors = ButtonDefaults.textButtonColors(
                    contentColor = MaterialTheme.colorScheme.error,
                ),
            ) {
                Text("Close tab")
            }
        }
    }
}
