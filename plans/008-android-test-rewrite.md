# Plan 008: Android test rewrite — characterization tests for ViewModel

> **Executor instructions**: Modifies Android tests. Run all verification.
>
> **Drift check**:
> ```bash
> cd /home/kevin/.hermes/projects/HermesCompanion
> git diff --stat 47041ea..HEAD -- app/src/test
> ```

## Status

- **Priority**: P2
- **Effort**: L
- **Risk**: LOW
- **Depends on**: 001
- **Category**: tests
- **Planned at**: commit `47041ea`, 2026-06-16

## Why this matters

`MainViewModelTest.kt` has 24+ tests named `*_doesNotCrash` or `*_noServer_setsError` that only verify no exception is thrown — they assert on the absence of failure, not on actual behavior. `SetupWizardTest.kt:309-377` tests local-variable state machine logic that doesn't exercise the production `SetupWizardScreen.kt`. These tests provide false confidence: zero actual behavior is verified, so regressions in error handling, state mutations, or DataStore persistence go undetected. This plan rewrites the ViewModel tests as real behavior-verifying tests using test doubles for `ApiClient` and `SessionManager`, and removes the no-op tests.

## Current state

**File**: `app/src/test/java/org/hermes/community/companion/data/MainViewModelTest.kt` (existing)

Currently contains 24+ tests of the pattern:
```kotlin
@Test
fun loadTasks_doesNotCrash() = runTest {
    viewModel.loadTasks()
    // No assertion — just expects no exception
}
```

**File**: `app/src/main/java/org/hermes/community/companion/MainViewModel.kt` (697 LOC)

Contains state flows for chat, kanban, settings. Functions to test:
- `loadSessions()`, `selectSession(id)`, `deleteSession(id)`
- `loadBoards()`, `loadTasks()`, `createTask()`, `completeTask()`, `commentOnTask()`, `assignTask()`
- `loadSettings()`, `saveSettings(url, user, pass, board)`
- `sendMessage(text)` and streaming variants
- Error state flows: `_chatError`, `_kanbanError`, `_settingsError`

**Consumers**: `ChatScreen.kt`, `KanbanScreen.kt`, `SettingsScreen.kt` — all read from ViewModel flows.

**Test infra**:
- `kotlinx-coroutines-test` for `runTest` and `TestDispatcher`
- MockK for mocking interfaces
- Turbine for flow testing
- JUnit 4

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Build | `cd /home/kevin/.hermes/projects/HermesCompanion && ./gradlew assembleDebug` | exit 0 |
| Test | `cd /home/kevin/.hermes/projects/HermesCompanion && ./gradlew test` | all pass |
| Specific test | `cd /home/kevin/.hermes/projects/HermesCompanion && ./gradlew test --tests "org.hermes.community.companion.MainViewModelBehaviorTest"` | new tests pass |
| Coverage | `cd /home/kevin/.hermes/projects/HermesCompanion && ./gradlew testDebugUnitTestCoverage` | report generated |

## Scope

**In scope**:
- `app/src/test/java/org/hermes/community/companion/data/FakeApiClient.kt` — create test double
- `app/src/test/java/org/hermes/community/companion/data/FakeSessionManager.kt` — create test double
- `app/src/test/java/org/hermes/community/companion/data/MainViewModelBehaviorTest.kt` — create
- `app/src/test/java/org/hermes/community/companion/data/MainViewModelTest.kt` — REPLACE with empty file or delete (the 24 no-op tests are removed)
- `app/src/test/java/org/hermes/community/companion/SetupWizardTest.kt` — REPLACE with behavior tests

**Out of scope**:
- `MainViewModel.kt` — no edits; this is test-side only
- UI tests (Compose UI tests for screens) — separate concern, requires Compose testing infra
- `ApiClient.kt` — no edits
- Daemon side — separate repo

## Git workflow

- Branch: `advisor/008-viewmodel-tests`
- Commit style: `test:` prefix
- Do NOT push, do NOT open a PR

## Steps

### Step 1: Drift check

```bash
cd /home/kevin/.hermes/projects/HermesCompanion
git diff --stat 47041ea..HEAD -- app/src/test
```

If tests changed, STOP.

### Step 2: Inventory existing tests

```bash
cd /home/kevin/.hermes/projects/HermesCompanion
ls app/src/test/java/org/hermes/community/companion/data/
wc -l app/src/test/java/org/hermes/community/companion/data/*.kt
```

Note current test count and file structure before replacing.

### Step 3: Create `FakeApiClient.kt`

A test double that implements the same interface as `ApiClient` and returns configurable responses.

```kotlin
package org.hermes.community.companion.data

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

class FakeApiClient : ApiClient {
    var listSessionsResponse: Result<List<Session>> = Result.success(emptyList())
    var listBoardsResponse: Result<List<Board>> = Result.success(emptyList())
    var listTasksResponse: Result<List<Task>> = Result.success(emptyList())
    var sendMessageResponse: Result<String> = Result.success("ok")
    var saveSettingsResult: Result<Unit> = Result.success(Unit)

    val savedSettings = mutableListOf<Settings>()
    val sentMessages = mutableListOf<String>()

    override suspend fun listSessions(): Result<List<Session>> = listSessionsResponse
    override suspend fun listBoards(slug: String?): Result<List<Board>> = listBoardsResponse
    override suspend fun listTasks(board: String, status: String?): Result<List<Task>> = listTasksResponse
    override suspend fun sendMessage(text: String, sessionId: String?): Result<String> {
        sentMessages.add(text)
        return sendMessageResponse
    }
    override suspend fun saveSettings(settings: Settings): Result<Unit> {
        savedSettings.add(settings)
        return saveSettingsResult
    }
    // ... implement other methods as needed
}
```

Adapt to match `ApiClient`'s actual interface. Read `ApiClient.kt` to verify method signatures.

### Step 4: Create `FakeSessionManager.kt`

```kotlin
package org.hermes.community.companion.data

import kotlinx.coroutines.flow.MutableStateFlow

class FakeSessionManager : SessionManagerApi {
    private val _baseUrl = MutableStateFlow("")
    private val _username = MutableStateFlow("")
    private val _password = MutableStateFlow("")
    private val _board = MutableStateFlow("default")

    override val baseUrl = _baseUrl
    override val username = _username
    override val password = _password
    override val board = _board

    var saveCount = 0

    override fun save(url: String, user: String, pass: String, board: String) {
        saveCount++
        _baseUrl.value = url
        _username.value = user
        _password.value = pass
        _board.value = board
    }
}
```

If `SessionManager` is an `object` (singleton) rather than an interface, refactoring to an interface is required. Check `SessionManager.kt` — if it's an object, this is a refactor and out of scope; instead, the test would need a Robolectric or instrumentation test (different scope).

If `SessionManager` is already an interface, the test is straightforward. If not, this plan should be reduced to testing only the public-flow behavior of ViewModel (which can be done via FakeApiClient alone) and skip the SessionManager interaction tests. Document the decision in the test file.

### Step 5: Create `MainViewModelBehaviorTest.kt`

```kotlin
package org.hermes.community.companion.data

import app.cash.turbine.test
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class MainViewModelBehaviorTest {
    private val testDispatcher = UnconfinedTestDispatcher()
    private lateinit var fakeApi: FakeApiClient
    private lateinit var viewModel: MainViewModel

    @Before
    fun setUp() {
        Dispatchers.setMain(testDispatcher)
        fakeApi = FakeApiClient()
        viewModel = MainViewModel(apiClient = fakeApi)
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    @Test
    fun `loadSessions populates session list on success`() = runTest {
        val sessions = listOf(Session(id = "1", title = "Test"))
        fakeApi.listSessionsResponse = Result.success(sessions)

        viewModel.sessions.test {
            viewModel.loadSessions()
            // Wait for the flow to update
            val state = awaitItem()
            assertEquals(sessions, state)
        }
    }

    @Test
    fun `loadSessions sets error on failure`() = runTest {
        fakeApi.listSessionsResponse = Result.failure(RuntimeException("network down"))

        viewModel.chatError.test {
            viewModel.loadSessions()
            // Error flow emits
            val err = awaitItem()
            assertNotNull(err)
            assertTrue(err!!.contains("network down") || err.contains("Request failed"))
        }
    }

    @Test
    fun `sendMessage appends to chat messages`() = runTest {
        fakeApi.sendMessageResponse = Result.success("response text")

        viewModel.chatMessages.test {
            viewModel.sendMessage("hello")
            // Expect at least 2 messages: the user message and the response
            val msgs = awaitItem()
            assertTrue(msgs.size >= 2)
            assertEquals("hello", msgs[0].text)
        }
    }

    @Test
    fun `sendMessage sets error on failure`() = runTest {
        fakeApi.sendMessageResponse = Result.failure(RuntimeException("api down"))

        viewModel.chatError.test {
            viewModel.sendMessage("hello")
            val err = awaitItem()
            assertNotNull(err)
        }
    }

    @Test
    fun `loadTasks filters by board slug`() = runTest {
        val tasks = listOf(Task(id = "1", title = "T1"), Task(id = "2", title = "T2"))
        fakeApi.listTasksResponse = Result.success(tasks)

        viewModel.tasks.test {
            viewModel.loadTasks("myboard")
            val result = awaitItem()
            assertEquals(tasks, result)
        }
    }

    @Test
    fun `completeTask updates task status locally`() = runTest {
        val initial = listOf(Task(id = "1", title = "T1", status = "todo"))
        fakeApi.listTasksResponse = Result.success(initial)

        viewModel.tasks.test {
            viewModel.loadTasks("myboard")
            // First emission: initial list
            skipItems(1)
            // Trigger complete
            viewModel.completeTask("1")
            // Next emission: task removed or status changed
            val updated = awaitItem()
            assertTrue(updated.none { it.id == "1" && it.status == "todo" })
        }
    }
}
```

Adapt to match the actual `MainViewModel` API. Read `MainViewModel.kt` to verify method names, flow names, and parameter shapes.

### Step 6: Replace `MainViewModelTest.kt`

The existing file has 24+ `_doesNotCrash` no-op tests. Delete the file (or replace with a single `// See MainViewModelBehaviorTest.kt` comment).

```bash
cd /home/kevin/.hermes/projects/HermesCompanion
git rm app/src/test/java/org/hermes/community/companion/data/MainViewModelTest.kt
```

### Step 7: Replace `SetupWizardTest.kt`

Same approach — delete and replace with behavior tests, or delete entirely if SetupWizardScreen has no public state to verify (UI test territory).

```bash
cd /home/kevin/.hermes/projects/HermesCompanion
# Either replace or delete:
git rm app/src/test/java/org/hermes/community/companion/SetupWizardTest.kt
```

### Step 8: Build and run tests

```bash
cd /home/kevin/.hermes/projects/HermesCompanion
./gradlew test --tests "org.hermes.community.companion.data.MainViewModelBehaviorTest"
```

If only some pass, the FakeApiClient may not match the real interface. Iterate.

### Step 9: Run full test suite

```bash
cd /home/kevin/.hermes/projects/HermesCompanion
./gradlew test
```

### Step 10: Commit

```bash
cd /home/kevin/.hermes/projects/HermesCompanion
git add -A
git status
git commit -m "$(cat <<'EOF'
test: rewrite ViewModel tests as behavior-verifying tests with test doubles

The previous MainViewModelTest.kt contained 24+ tests named *_doesNotCrash
that only verified no exception was thrown, providing false confidence
in the ViewModel's behavior. SetupWizardTest.kt tested local-variable
state machine logic that did not exercise the production code.

Changes:
- Add FakeApiClient and FakeSessionManager test doubles
- Add MainViewModelBehaviorTest.kt with real behavior assertions
  using Turbine for flow testing and runTest for coroutine control
- Remove the no-op MainViewModelTest.kt
- Remove or replace SetupWizardTest.kt
- Tests now verify: loadSessions populates state, sendMessage appends,
  loadTasks filters by board, completeTask updates local state, error
  paths set error flows
EOF
)"
```

## Test plan

- New `MainViewModelBehaviorTest.kt` — 6+ behavior tests
- Coverage of: success paths, error paths, state mutations
- Use Turbine for StateFlow testing
- Use UnconfinedTestDispatcher for instant coroutine execution
- Verification: `./gradlew test --tests "org.hermes.community.companion.data.MainViewModelBehaviorTest"` — all pass

## Done criteria

- [ ] `./gradlew assembleDebug` exits 0
- [ ] `./gradlew test` exits 0
- [ ] `grep "doesNotCrash" app/src/test` returns no matches
- [ ] `grep "MainViewModelBehaviorTest" app/src/test` shows 1+ matches
- [ ] `test -f app/src/test/java/org/hermes/community/companion/data/FakeApiClient.kt` returns 0
- [ ] `git status` clean
- [ ] `plans/README.md` Plan 008 row updated to `DONE`

## STOP conditions

- Plan 001 not DONE — STOP.
- Drift check shows test files changed — STOP.
- `ApiClient` is a final class or object — STOP, refactor to interface first (separate plan).
- `SessionManager` is an object — STOP, either skip SessionManager tests or refactor (separate plan).
- Tests don't compile because ViewModel's public API is different from what this plan assumes — STOP, read the actual MainViewModel.kt and adjust the test code.

## Maintenance notes

- When `MainViewModel` is refactored, this test will catch regressions. That's the whole point.
- For Compose UI testing, add a separate plan using `androidx.compose.ui.test.junit4.createComposeRule()`. Out of scope here.
- The `FakeApiClient` should grow as the real `ApiClient` grows. Add new methods as they're added to the interface.
- For v2: add integration tests that run the daemon and the ViewModel together (verifying real network contracts). Robolectric or instrumented tests.
