package io.github.nanomuse.sync

import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Contract C7's client rules, against the fake relay: ids, adoption, merge, tombstones, cursor, the switch. */
class SyncEngineTest {
    private val uuid = Regex("^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")

    /** A phone; the C7/C8 tests below were written with side chats on, C9's own tests set it per case. */
    private class Phone(val relay: FakeRelay, val device: String, account: String = "acct-1", sideChats: Boolean = true) {
        val store = MemorySyncStore()
        val chats = MemoryChats()
        var clock = 1_700_000_000_000L
        var sideChats = sideChats
        var signedIn = true
        val engine = SyncEngine(store, chats, relay, device, account, now = { clock }, sideChats = { this.sideChats }, isCurrentAccount = { signedIn })
    }

    private fun phone(relay: FakeRelay, device: String, account: String = "acct-1", sideChats: Boolean = true) = Phone(relay, device, account, sideChats)

    @Test fun `first push mints a cid per chat and a mid per message, main as main`() = runTest {
        val relay = FakeRelay()
        val p = phone(relay, "phone-a")
        p.chats.addSession("main"); p.chats.main = "main"
        p.chats.addSession("side", "Trip")
        p.chats.user("main", "hi"); p.chats.assistant("main", "hello")
        p.chats.user("side", "plan a trip"); p.chats.assistant("side", "where to?")

        val accepted = p.engine.push()

        assertEquals(6, accepted)
        val mainMap = p.store.convs.getValue("main")
        val sideMap = p.store.convs.getValue("side")
        assertEquals("main", mainMap.kind)
        assertEquals("side", sideMap.kind)
        assertTrue(uuid.matches(mainMap.cid))
        assertTrue(uuid.matches(sideMap.cid))
        assertTrue(mainMap.pushed && sideMap.pushed)
        assertEquals("Trip", sideMap.pushedTitle)
        assertEquals(4, p.store.msgs.size)
        assertTrue(p.store.msgs.values.all { it.pushed && uuid.matches(it.mid) })
        assertEquals(setOf("main", "side"), relay.convs.values.map { it.kind }.toSet())
        assertEquals(4, relay.msgs.size)
        // the cursor advanced through the pull that comes before a first push
        assertNotNull(p.engine.cursor())
        // nothing new: nothing sent
        val before = relay.pushes
        assertEquals(0, p.engine.push())
        assertEquals(before, relay.pushes)
    }

    @Test fun `the same mids go out again only when they were never accepted`() = runTest {
        val relay = FakeRelay()
        val p = phone(relay, "phone-a")
        p.chats.addSession("main"); p.chats.main = "main"
        p.chats.user("main", "one")
        p.engine.pull() // the first pull is behind us; the POST itself is what fails
        relay.failWith = SyncException(0, "unreachable", "offline")
        val failed = runCatching { p.engine.push() }.exceptionOrNull()
        assertTrue(failed is SyncException)
        relay.failWith = null
        val minted = p.store.msgs.values.single().mid
        assertEquals(2, p.engine.push())
        assertEquals(minted, relay.msgs.keys.single())
    }

    @Test fun `late push response cannot mark messages sent after account changes`() = runTest {
        val relay = FakeRelay()
        val p = phone(relay, "phone-a")
        p.chats.addSession("main"); p.chats.main = "main"
        p.engine.pull()
        val id = p.chats.user("main", "private")
        relay.onPushReply = { p.signedIn = false }
        val failure = runCatching { p.engine.push() }.exceptionOrNull() as SyncException
        assertEquals("account_changed", failure.code)
        assertFalse(p.store.msgs.getValue(id).pushed)
    }

    @Test fun `late pull response cannot populate a different account`() = runTest {
        val relay = FakeRelay()
        relay.seed("desk", "private-cid", "main", "A", 1)
        relay.seedMessage("desk", "private-cid", "private-mid", "user", "private", 2)
        val p = phone(relay, "phone-a")
        relay.onChangesReply = { p.signedIn = false }
        val failure = runCatching { p.engine.pull() }.exceptionOrNull() as SyncException
        assertEquals("account_changed", failure.code)
        assertTrue(p.chats.sessions.isEmpty())
        assertTrue(p.store.msgs.isEmpty())
        assertNull(p.engine.cursor())
    }

    @Test fun `message identifier collision and deleted conversation remain pending`() = runTest {
        for (reason in listOf("known_mid", "conversation_deleted")) {
            val relay = FakeRelay()
            val p = phone(relay, "phone-a")
            p.chats.addSession("main"); p.chats.main = "main"
            p.engine.pull()
            val id = p.chats.user("main", "pending")
            relay.refusal = { convs, messages ->
                val rejection = if (reason == "known_mid") Rejection(messages.single().mid, null, reason, null)
                    else Rejection(null, convs.single().cid, reason, null)
                PushResult(0, 0, listOf(rejection))
            }
            val failure = runCatching { p.engine.push() }.exceptionOrNull() as SyncException
            assertEquals("sync_rejected", failure.code)
            assertFalse(p.store.msgs.getValue(id).pushed)
            if (reason == "conversation_deleted") assertFalse(p.store.convs.getValue("main").pushed)
        }
    }

    @Test fun `missing local chat mapping never deletes the cloud conversation`() = runTest {
        val relay = FakeRelay()
        val p = phone(relay, "phone-a")
        p.chats.addSession("main"); p.chats.main = "main"
        p.chats.user("main", "keep")
        p.engine.push()
        val cid = p.store.convs.getValue("main").cid
        p.chats.deleteSession("main") // Simulate a lost row, not an explicit user deletion.
        val failure = runCatching { p.engine.push() }.exceptionOrNull() as SyncException
        assertEquals("sync_rejected", failure.code)
        assertFalse(relay.convs.getValue(cid).deleted)
        assertTrue(relay.deletes.isEmpty())
        assertEquals(cid, p.store.convs.getValue("main").cid)
    }

    @Test fun `known mid is confirmed only when remote text and conversation match`() = runTest {
        for (same in listOf(true, false)) {
            val relay = FakeRelay()
            val p = phone(relay, "phone-a")
            p.chats.addSession("main"); p.chats.main = "main"
            p.engine.pull()
            val id = p.chats.user("main", "same")
            relay.refusal = { convs, messages ->
                val message = messages.single()
                relay.seedMessage("phone-a", message.cid, message.mid, message.role, if (same) message.text else "different", message.createdAt)
                PushResult(0, 0, listOf(Rejection(message.mid, null, "known_mid", null)))
            }
            val result = runCatching { p.engine.push() }
            assertEquals(same, result.isSuccess)
            assertEquals(same, p.store.msgs.getValue(id).pushed)
        }
    }

    @Test fun `partial message rejection leaves only refused row pending`() = runTest {
        val relay = FakeRelay()
        val p = phone(relay, "phone-a")
        p.chats.addSession("main"); p.chats.main = "main"
        p.engine.pull()
        val accepted = p.chats.user("main", "accepted")
        val refused = p.chats.user("main", "refused")
        relay.refusal = { _, messages ->
            PushResult(0, 1, listOf(Rejection(messages.last().mid, null, "known_mid", null)))
        }
        val error = runCatching { p.engine.push() }.exceptionOrNull() as SyncException
        assertEquals("sync_rejected", error.code)
        assertTrue(p.store.msgs.getValue(accepted).pushed)
        assertFalse(p.store.msgs.getValue(refused).pushed)
    }

    @Test fun `many known mids are verified in one bounded batch`() = runTest {
        val relay = FakeRelay()
        val p = phone(relay, "phone-a")
        p.chats.addSession("main"); p.chats.main = "main"
        p.engine.pull()
        repeat(200) { p.chats.user("main", "row-$it") }
        relay.refusal = { _, messages ->
            messages.forEach { relay.seedMessage("phone-a", it.cid, it.mid, it.role, it.text, it.createdAt) }
            PushResult(0, 0, messages.map { Rejection(it.mid, null, "known_mid", null) })
        }
        p.engine.push()
        assertEquals(1, relay.lookupCalls)
        assertTrue(p.store.msgs.values.all { it.pushed })
        val budget = LookupBudget(70)
        assertEquals(70, (0 until 200 * 70).count { budget.take() })
        assertFalse(budget.take())
    }

    @Test fun `foreign account or conversation mid collision never deletes or hides local message`() = runTest {
        for (deleted in listOf(false, true)) for (foreignAccount in listOf(false, true)) {
            val relay = FakeRelay()
            val p = phone(relay, "phone-b", account = "account-b")
            p.chats.addSession("local-a", "A")
            val localId = p.chats.user("local-a", "private A")
            p.store.putConversation(SyncConversation("local-a", "cid-a", "side", "a", "", null, true,
                owner = if (foreignAccount) "account-a" else "account-b"))
            p.store.putMessages(listOf(SyncMessage(localId, "same-mid", "local-a", pushed = true)))
            p.store.putMeta(SyncStore.ACCOUNT, "account-b")
            relay.seed("other", "cid-b", "side", "B", 1)
            val remote = relay.seedMessage("other", "cid-b", "same-mid", "user", "B content", 2)
            remote.deleted = deleted
            val failure = runCatching { p.engine.pull() }.exceptionOrNull() as SyncException
            assertEquals("sync_rejected", failure.code)
            assertEquals("private A", Transcript.items(p.chats.messages("local-a")).single().text)
            assertEquals("local-a", p.store.messageByMid("same-mid")?.sessionId)
            assertNull(p.store.conversationByCid("cid-b")) // no mapping survives an aborted chat transaction
        }
    }

    @Test fun `lost deletion acknowledgement is resolved only for the same remote tombstone`() = runTest {
        for (remoteDeleted in listOf(false, true)) {
            val relay = FakeRelay()
            val p = phone(relay, "phone-a")
            p.chats.addSession("main"); p.chats.main = "main"
            val id = p.chats.user("main", "old")
            p.engine.push()
            val mid = p.store.msgs.getValue(id).mid
            p.chats.removeRow(id)
            relay.refusal = { _, messages ->
                relay.msgs.getValue(mid).deleted = remoteDeleted
                PushResult(0, 0, messages.map { Rejection(it.mid, null, "known_mid", null) })
            }
            val result = runCatching { p.engine.push() }
            assertEquals(remoteDeleted, result.isSuccess)
            assertEquals(!remoteDeleted, p.store.msgs.containsKey(id))
        }
    }

    @Test fun `history cap keeps pending rows and retries the same chat after a separate deletion`() = runTest {
        val relay = FakeRelay()
        val p = phone(relay, "phone-a", account = "account-a")
        p.chats.addSession("main"); p.chats.main = "main"
        val old = p.chats.user("main", "old")
        p.engine.push()
        val cid = p.store.convs.getValue("main").cid
        // Another account's chat remains local and must not be pushed on retry.
        p.chats.addSession("foreign", "other account")
        p.chats.user("foreign", "not mine")
        p.store.putConversation(SyncConversation("foreign", "foreign-cid", "side", "b", "", null, true, owner = "account-b"))
        relay.messageLimit = 1
        val fresh = p.chats.user("main", "new")
        val failure = runCatching { p.engine.push() }.exceptionOrNull() as SyncException
        assertEquals("message_limit", failure.code)
        val pending = p.store.msgs.getValue(fresh)
        assertFalse(pending.pushed)
        assertEquals(cid, p.store.convs.getValue("main").cid)

        p.chats.removeRow(old)
        p.engine.push()
        assertEquals(pending.mid, p.store.msgs.getValue(fresh).mid)
        assertTrue(p.store.msgs.getValue(fresh).pushed)
        assertEquals(listOf("new"), relay.msgs.values.filterNot { it.deleted }.map { it.text })
        assertTrue(relay.convs["foreign-cid"] == null)
    }

    @Test fun `per-row history refusal remains pending rather than marked sent`() = runTest {
        val relay = FakeRelay()
        val p = phone(relay, "phone-a")
        p.chats.addSession("main"); p.chats.main = "main"
        p.engine.pull()
        relay.messageLimit = 0
        relay.capAsRowRejection = true
        val id = p.chats.user("main", "pending")
        val failure = runCatching { p.engine.push() }.exceptionOrNull() as SyncException
        assertEquals("message_limit", failure.code)
        assertFalse(p.store.msgs.getValue(id).pushed)
        val mid = p.store.msgs.getValue(id).mid
        relay.messageLimit = 1
        p.engine.push()
        assertEquals(mid, p.store.msgs.getValue(id).mid)
        assertTrue(p.store.msgs.getValue(id).pushed)
    }

    @Test fun `main chat adoption on the first pull`() = runTest {
        val relay = FakeRelay(mapOf("desk-1" to "Mac"))
        relay.seed("desk-1", "cid-main", "main", null, 1_700_000_000)
        relay.seedMessage("desk-1", "cid-main", "mid-1", "user", "from the mac", 1_700_000_001)
        relay.seedMessage("desk-1", "cid-main", "mid-2", "assistant", "the mac answers", 1_700_000_002)
        val p = phone(relay, "phone-a")
        p.chats.addSession("main"); p.chats.main = "main"
        p.chats.user("main", "from the phone", at = 1_700_000_010_000)

        p.engine.push() // pulls first

        val map = p.store.convs.getValue("main")
        assertEquals("cid-main", map.cid)
        assertEquals("main", map.kind)
        // the mac's two lines are in the phone's main chat, in time order, before the phone's own
        val texts = p.chats.messages("main").map { Transcript.items(listOf(it)).firstOrNull()?.text }
        assertEquals(listOf("from the mac", "the mac answers", "from the phone"), texts)
        // and the phone's line went up under the adopted cid
        assertEquals(setOf("cid-main"), relay.msgs.values.map { it.cid }.toSet())
        assertEquals(3, relay.msgs.size)
        // the mac's two rows carry its name; the phone's own row carries nothing
        val rows = p.chats.messages("main")
        assertEquals(mapOf(rows[0].id to "Mac", rows[1].id to "Mac"), p.engine.captions())
    }

    @Test fun `no main chat yet - the remote one becomes it`() = runTest {
        val relay = FakeRelay(mapOf("desk-1" to "Mac"))
        relay.seed("desk-1", "cid-main", "main", "Main", 1_700_000_000)
        relay.seedMessage("desk-1", "cid-main", "mid-1", "user", "hello", 1_700_000_001)
        val p = phone(relay, "phone-a")

        p.engine.pull()

        val main = p.chats.mainSessionId()
        assertNotNull(main)
        assertEquals("cid-main", p.store.convs.getValue(main!!).cid)
        assertEquals(1, p.chats.messages(main).size)
    }

    @Test fun `two devices race for main - the loser re-posts under cid_main`() = runTest {
        val relay = FakeRelay()
        val a = phone(relay, "phone-a")
        a.chats.addSession("main"); a.chats.main = "main"
        a.chats.user("main", "a says")
        a.engine.push()
        val cidA = a.store.convs.getValue("main").cid

        // b pulled while the relay was still empty, then a pushed; b now pushes its own main
        val b = phone(relay, "phone-b")
        b.chats.addSession("main"); b.chats.main = "main"
        b.store.meta[SyncStore.ACCOUNT] = "acct-1"
        b.store.meta[SyncStore.CURSOR] = "0"
        b.chats.user("main", "b says")
        b.engine.push()

        assertEquals(cidA, b.store.convs.getValue("main").cid)
        assertEquals(1, relay.convs.values.count { it.kind == "main" })
        assertEquals(setOf("a says", "b says"), relay.msgs.values.filter { it.cid == cidA }.map { it.text }.toSet())
        assertTrue(b.store.msgs.values.all { it.pushed })
    }

    @Test fun `a side chat from another device appears as a normal chat, its rows captioned, and can be continued`() = runTest {
        val relay = FakeRelay(mapOf("pixel" to "Pixel 8"))
        relay.seed("pixel", "cid-s", "side", "Groceries", 1_700_000_000)
        relay.seedMessage("pixel", "cid-s", "mid-1", "user", "milk, eggs", 1_700_000_001, listOf(Attachment("list.pdf", "application/pdf", 12)))
        relay.seedMessage("pixel", "cid-s", "mid-2", "assistant", "noted", 1_700_000_002)
        val p = phone(relay, "phone-a")

        val r = p.engine.pull()

        assertEquals(3, r.applied)
        val local = p.chats.sessions.values.single()
        assertEquals("Groceries", local.title)
        val rows = p.chats.messages(local.id)
        assertEquals(2, rows.size)
        assertTrue(rows[0].partsJson.contains("[Attachment list.pdf]"))
        // per message, not per chat: each pulled row says where it was written
        assertEquals(rows.associate { it.id to "Pixel 8" }, p.engine.captions())
        // the person goes on here: the new turn syncs back under the same cid
        p.chats.user(local.id, "and bread"); p.chats.assistant(local.id, "added")
        p.engine.push()
        assertEquals(4, relay.msgs.values.count { it.cid == "cid-s" })
        assertEquals(2, p.engine.captions().size) // this phone's own two rows carry no caption
        // the second pull brings nothing new: known mids are left alone
        assertEquals(0, p.engine.pull().applied)
        assertEquals(4, p.chats.messages(local.id).size)
    }

    @Test fun `tombstones - a deleted message and a deleted chat go away locally`() = runTest {
        val relay = FakeRelay(mapOf("pixel" to "Pixel 8"))
        relay.seed("pixel", "cid-s", "side", "Groceries", 1_700_000_000)
        relay.seedMessage("pixel", "cid-s", "mid-1", "user", "milk", 1_700_000_001)
        relay.seedMessage("pixel", "cid-s", "mid-2", "assistant", "noted", 1_700_000_002)
        val p = phone(relay, "phone-a")
        p.engine.pull()
        val local = p.chats.sessions.keys.single()

        relay.msgs.getValue("mid-2").apply { deleted = true; seq = ++relay.seq }
        p.engine.pull()
        assertEquals(1, p.chats.messages(local).size)

        relay.deleteConversation("cid-s")
        p.engine.pull()
        assertTrue(p.chats.sessions.isEmpty())
        assertTrue(p.store.convs.isEmpty())
        assertTrue(p.store.msgs.isEmpty())
    }

    @Test fun `an edit here is a new mid plus a tombstone, a deleted chat a tombstone plus DELETE`() = runTest {
        val relay = FakeRelay()
        val p = phone(relay, "phone-a")
        p.chats.addSession("main"); p.chats.main = "main"
        p.chats.addSession("side", "Old")
        val q = p.chats.user("side", "qustion")
        p.chats.assistant("side", "answer")
        p.engine.push()
        val oldMid = p.store.msgs.getValue(q).mid

        // edited: the row is cut and written again
        p.chats.removeRow(q)
        p.chats.user("side", "question", at = 1_700_000_000_500)
        p.engine.push()
        assertTrue(relay.msgs.getValue(oldMid).deleted)
        assertEquals(1, relay.msgs.values.count { !it.deleted && it.role == "user" && it.text == "question" })
        assertNull(p.store.msgs[q])

        // renamed
        p.chats.sessions.getValue("side").title = "New"
        p.engine.push()
        assertEquals("New", relay.convs.values.first { it.kind == "side" }.title)

        // deleted
        val cid = p.store.convs.getValue("side").cid
        p.store.putConversation(p.store.convs.getValue("side").copy(deleted = true))
        p.chats.deleteSession("side")
        p.engine.push()
        assertTrue(relay.convs.getValue(cid).deleted)
        assertEquals(listOf(cid), relay.deletes)
        assertNull(p.store.convs["side"])
        assertTrue(p.store.msgs.values.none { it.sessionId == "side" })
    }

    @Test fun `a rename on another device lands here`() = runTest {
        val relay = FakeRelay()
        val p = phone(relay, "phone-a")
        p.chats.addSession("main"); p.chats.main = "main"
        p.chats.addSession("side", "Old")
        p.chats.user("side", "x")
        p.engine.push()
        val cid = p.store.convs.getValue("side").cid
        relay.convs.getValue(cid).apply { title = "Renamed elsewhere"; seq = ++relay.seq }

        val r = p.engine.pull()

        assertEquals("Renamed elsewhere", p.chats.sessions.getValue("side").title)
        assertTrue("side" in r.touchedSessions)
        // and it is not pushed back as a change of ours
        assertEquals(0, p.engine.push())
    }

    @Test fun `cursor is kept and pages are followed`() = runTest {
        val relay = FakeRelay(mapOf("pixel" to "Pixel 8"))
        relay.seed("pixel", "cid-s", "side", "Long", 1_700_000_000)
        repeat(1200) { relay.seedMessage("pixel", "cid-s", "mid-$it", if (it % 2 == 0) "user" else "assistant", "line $it", 1_700_000_001L + it) }
        val p = phone(relay, "phone-a")

        // the first pull after sign-in is the tail (C9): the chat and its newest 300 rows, in one page
        val r = p.engine.pull()

        assertEquals(1 + SyncEngine.TAIL, r.applied)
        assertEquals(1200 - SyncEngine.TAIL, r.skipped)
        assertEquals(relay.seq, r.cursor)
        assertEquals(relay.seq.toString(), p.store.meta[SyncStore.CURSOR])
        // later changes only, in pages of 500 followed to the end
        repeat(1200) { relay.seedMessage("pixel", "cid-s", "mid-new-$it", "user", "more $it", 1_700_002_000L + it) }
        val r2 = p.engine.pull()
        assertEquals(1200, r2.applied)
        assertEquals(0, r2.skipped)
        assertEquals(relay.seq, r2.cursor)
        assertEquals(1500, p.chats.messages(p.store.conversationByCid("cid-s")!!.sessionId).size)
    }

    @Test fun `a push of many messages goes in batches of 200`() = runTest {
        val relay = FakeRelay()
        val p = phone(relay, "phone-a")
        p.chats.addSession("main"); p.chats.main = "main"
        repeat(250) { p.chats.user("main", "q$it"); p.chats.assistant("main", "a$it") }
        p.engine.push()
        assertEquals(listOf(200, 200, 100), relay.pushedBatches)
        assertEquals(500, relay.msgs.size)
    }

    @Test fun `another account starts with its own cursor and none of the first account's chats`() = runTest {
        val relay = FakeRelay()
        val p = phone(relay, "phone-a", account = "acct-1")
        p.chats.addSession("main"); p.chats.main = "main"
        p.chats.user("main", "hi")
        p.engine.push()
        val relay2 = FakeRelay()
        val q = SyncEngine(p.store, p.chats, relay2, "phone-a", "acct-2", sideChats = { true })
        q.push()
        assertEquals("acct-2", p.store.meta[SyncStore.ACCOUNT])
        // C10: the first account's chat is not pushed into the second; its mapping is kept for its return
        assertEquals(0, relay2.msgs.size)
        assertEquals("acct-1", p.store.convs.getValue("main").owner)
        assertNull(p.chats.main) // the home belongs to acct-1; acct-2 starts on a draft
    }

    @Test fun `sync_off is a 409 the caller sees, the switch on re-pushes everything`() = runTest {
        val relay = FakeRelay()
        val p = phone(relay, "phone-a")
        p.chats.addSession("main"); p.chats.main = "main"
        p.chats.user("main", "hi")
        p.engine.push()
        p.engine.setEnabled(false)
        assertTrue(relay.convs.isEmpty() && relay.msgs.isEmpty())
        val e = runCatching { p.engine.pull() }.exceptionOrNull() as? SyncException
        assertEquals(409, e?.status)
        assertEquals("sync_off", e?.code)

        p.engine.setEnabled(true)
        assertNull(p.engine.cursor())
        assertEquals(2, p.engine.push())
        assertEquals(1, relay.msgs.size)
        assertEquals(1, relay.convs.size)
    }

    @Test fun `delete synced conversations wipes the relay and keeps the chats here`() = runTest {
        val relay = FakeRelay()
        val p = phone(relay, "phone-a")
        p.chats.addSession("main"); p.chats.main = "main"
        p.chats.user("main", "hi")
        p.engine.push()
        p.engine.wipe()
        assertEquals(1, relay.wipes)
        assertTrue(relay.msgs.isEmpty())
        assertEquals(1, p.chats.messages("main").size)
        assertFalse(p.store.convs.isEmpty())
    }

    // ── contract C8 ───────────────────────────────────────────────────────

    @Test fun `C8 backfill - the whole history goes out oldest first, the first conversation's opening and naming fence included`() = runTest {
        val relay = FakeRelay()
        val p = phone(relay, "phone-a")
        // the first conversation, weeks ago: the scripted opening is virtual, the rows are real
        p.chats.addSession("first", createdAt = 1_690_000_000_000L); p.chats.main = "first"
        p.chats.preludes["first"] = listOf(TranscriptItem("nm-intro:first", "assistant", "Hello. What should I call you?", emptyList(), 1_689_999_999_000L))
        p.chats.user("first", "Call me Bob", at = 1_690_000_001_000L)
        val fence = "Bob it is. What would you like to call me?\n\n```nanomuse-naming\n{\"user_address\": \"Bob\", \"suggest\": [\"Pip\", \"Wren\"]}\n```"
        p.chats.assistant("first", fence, at = 1_690_000_002_000L)
        p.chats.user("first", "Pip", at = 1_690_000_003_000L)
        p.chats.assistant("first", "Pip, then.\n\n```nanomuse-naming\n{\"agent_name\": \"Pip\"}\n```", at = 1_690_000_004_000L)
        // a side chat in between, and more in the main chat later
        p.chats.addSession("trip", "Trip", createdAt = 1_690_000_100_000L)
        p.chats.user("trip", "plan a trip", at = 1_690_000_100_000L); p.chats.assistant("trip", "where to?", at = 1_690_000_101_000L)
        p.chats.user("first", "later question", at = 1_690_000_200_000L); p.chats.assistant("first", "later answer", at = 1_690_000_201_000L)

        // sign-in: the pull first (nothing), then everything
        p.engine.push()

        val sent = relay.msgs.values.sortedBy { it.seq }
        assertEquals(
            listOf("Hello. What should I call you?", "Call me Bob", fence, "Pip", "Pip, then.\n\n```nanomuse-naming\n{\"agent_name\": \"Pip\"}\n```",
                "plan a trip", "where to?", "later question", "later answer"),
            sent.map { it.text },
        )
        assertEquals(sent.map { it.createdAt }, sent.map { it.createdAt }.sorted())
        assertEquals("main", relay.convs.getValue(p.store.convs.getValue("first").cid).kind)
        // the opening is mapped like a row and is not sent twice
        assertEquals(0, p.engine.push())
        assertEquals(9, relay.msgs.size)
    }

    @Test fun `C8 push at send - the person's line goes out while the turn runs, the reply when it ends, nothing twice`() = runTest {
        val relay = FakeRelay()
        val p = phone(relay, "phone-a")
        p.chats.addSession("main"); p.chats.main = "main"
        p.chats.user("main", "do the thing")

        p.engine.push() // ConversationSync.sent(): right after the row is written
        assertEquals(listOf("user" to "do the thing"), relay.msgs.values.map { it.role to it.text })

        // the turn runs: tool steps are not the conversation; the lone user row is not sent again
        p.chats.assistantTool("main", "On it."); p.chats.toolResult("main")
        assertEquals(0, p.engine.push())
        assertEquals(1, relay.msgs.size)

        // the turn ends: the final text goes, under a mid of its own
        p.chats.assistant("main", "Done.")
        p.engine.push()
        assertEquals(listOf("user" to "do the thing", "assistant" to "Done."), relay.msgs.values.sortedBy { it.seq }.map { it.role to it.text })
        assertEquals(2, relay.msgs.keys.toSet().size)
    }

    @Test fun `C8 main merge - the local main shows the union with the relay's rows in time order, by mid once`() = runTest {
        val relay = FakeRelay(mapOf("desk-1" to "Mac"))
        relay.seed("desk-1", "cid-main", "main", null, 1_700_000_000)
        relay.seedMessage("desk-1", "cid-main", "mid-0", "user", "mac 0", 1_700_000_000)
        relay.seedMessage("desk-1", "cid-main", "mid-2", "assistant", "mac 2", 1_700_000_002)
        relay.seedMessage("desk-1", "cid-main", "mid-4", "user", "mac 4", 1_700_000_004)
        val p = phone(relay, "phone-a")
        p.chats.addSession("main"); p.chats.main = "main"
        p.chats.user("main", "phone 1", at = 1_700_000_001_000)
        p.chats.assistant("main", "phone 3", at = 1_700_000_003_000)

        p.engine.push() // pull, adopt, merge, then ours go up

        val texts = p.chats.messages("main").map { Transcript.items(listOf(it)).first().text }
        assertEquals(listOf("mac 0", "phone 1", "mac 2", "phone 3", "mac 4"), texts)
        // one row per mid, here and there
        assertEquals(5, p.store.msgs.values.map { it.mid }.toSet().size)
        assertEquals(5, relay.msgs.size)
        assertEquals(1, relay.convs.size)
        // the mac's rows between ours are not "steps" of our turns: no tombstone goes out for them
        assertTrue(relay.msgs.values.none { it.deleted })
        // nothing more to say in either direction
        assertEquals(0, p.engine.pull().applied)
        assertEquals(0, p.engine.push())
        assertEquals(5, p.chats.messages("main").size)
    }

    @Test fun `C8 echo - a message this phone pushed comes back and makes no second row`() = runTest {
        val relay = FakeRelay(mapOf("phone-a" to "Pixel 8"))
        val p = phone(relay, "phone-a")
        p.chats.addSession("main"); p.chats.main = "main"
        p.chats.user("main", "hi"); p.chats.assistant("main", "hello")
        p.engine.push()
        val cid = p.store.convs.getValue("main").cid

        // the relay lists our rows as changes after the cursor we pushed at (as a real relay does)
        relay.msgs.values.forEach { it.seq = ++relay.seq }
        assertEquals(0, p.engine.pull().applied)
        assertEquals(2, p.chats.messages("main").size)
        assertTrue(p.engine.captions().isEmpty())

        // a row of ours under a mid this phone no longer maps (the mapping was lost): still ours, still skipped
        relay.seedMessage("phone-a", cid, "mid-lost", "user", "hi", 1_700_000_000)
        assertEquals(0, p.engine.pull().applied)
        assertEquals(2, p.chats.messages("main").size)

        // another device's row under the same cid is new, and captioned
        relay.seedMessage("desk-1", cid, "mid-desk", "user", "from the desk", 1_700_000_100)
        assertEquals(1, p.engine.pull().applied)
        assertEquals(3, p.chats.messages("main").size)
    }

    @Test fun `a long text is cut at what the relay keeps`() = runTest {
        val relay = FakeRelay()
        val p = phone(relay, "phone-a")
        p.chats.addSession("main"); p.chats.main = "main"
        p.chats.user("main", "x".repeat(40_000))
        p.engine.push()
        assertEquals(SyncEngine.TEXT_MAX, relay.msgs.values.single().text.length)
    }

    // ── contract C9: main first ───────────────────────────────────────────

    @Test fun `C9 scope - with side chats off only the main chat goes out and the pull asks for scope=main`() = runTest {
        val relay = FakeRelay()
        val p = phone(relay, "phone-a", sideChats = false)
        p.chats.addSession("main"); p.chats.main = "main"
        p.chats.addSession("side", "Trip")
        p.chats.user("main", "hi"); p.chats.assistant("main", "hello")
        p.chats.user("side", "plan a trip"); p.chats.assistant("side", "where to?")

        assertEquals(3, p.engine.push())

        assertEquals(setOf("main"), relay.convs.values.map { it.kind }.toSet())
        assertEquals(2, relay.msgs.size)
        assertNull(p.store.convs["side"])
        assertTrue(relay.changeCalls.all { it.second == SyncApi.SCOPE_MAIN })
        // the first pull after sign-in asks for the tail; the ones after it do not
        assertEquals(Triple(0L, SyncApi.SCOPE_MAIN, SyncEngine.TAIL), relay.changeCalls.first())
        p.engine.pull()
        assertEquals(0, relay.changeCalls.last().third)
    }

    @Test fun `C9 scope - a side row that arrives while the switch is off makes no chat and no row here`() = runTest {
        val relay = FakeRelay(mapOf("pixel" to "Pixel 8"))
        relay.seed("pixel", "cid-m", "main", null, 1_700_000_000)
        relay.seedMessage("pixel", "cid-m", "mid-m", "user", "main line", 1_700_000_001)
        relay.seed("pixel", "cid-s", "side", "Groceries", 1_700_000_000)
        relay.seedMessage("pixel", "cid-s", "mid-s", "user", "milk", 1_700_000_002)
        val p = phone(relay, "phone-a", sideChats = false)
        p.chats.addSession("main"); p.chats.main = "main"

        // the fake honours scope=main; make it send the side rows anyway, as a relay from before C9 would
        relay.ignoreScope = true
        assertEquals(2, relay.changes(0, 500, SyncApi.SCOPE_MAIN, 0).conversations.size)
        p.engine.pull()

        assertEquals(1, p.chats.sessions.size)
        assertEquals(listOf("main line"), p.chats.messages("main").map { Transcript.items(listOf(it)).first().text })
        assertNull(p.store.conversationByCid("cid-s"))
    }

    @Test fun `C9 tail - the first pull after sign-in gets the newest rows, the rest is reported as skipped, the cursor is the latest`() = runTest {
        val relay = FakeRelay(mapOf("desk-1" to "Mac"))
        relay.seed("desk-1", "cid-main", "main", null, 1_700_000_000)
        for (i in 1..(SyncEngine.TAIL + 50)) relay.seedMessage("desk-1", "cid-main", "mid-$i", if (i % 2 == 1) "user" else "assistant", "line $i", 1_700_000_000L + i)
        val p = phone(relay, "phone-a", sideChats = false)

        val r = p.engine.pull()

        assertEquals(50, r.skipped)
        assertEquals(SyncEngine.TAIL, p.store.msgs.size)
        val main = p.chats.mainSessionId()!!
        val texts = p.chats.messages(main).map { Transcript.items(listOf(it)).first().text }
        assertEquals("line 51", texts.first())
        assertEquals("line ${SyncEngine.TAIL + 50}", texts.last())
        assertEquals(relay.seq, r.cursor)
        // a later pull continues from the cursor: nothing older comes, something newer does
        relay.seedMessage("desk-1", "cid-main", "mid-new", "assistant", "newest", 1_700_000_900)
        val r2 = p.engine.pull()
        assertEquals(1, r2.applied)
        assertEquals(0, r2.skipped)
        assertEquals(SyncEngine.TAIL + 1, p.store.msgs.size)
        // the relay's assistant row is reported, so the "working" line can go
        assertEquals(setOf(main), r2.replied)
    }

    @Test fun `C9 switch - turning side chats on pulls once from zero with scope=all and backfills this phone's side chats`() = runTest {
        val relay = FakeRelay(mapOf("pixel" to "Pixel 8"))
        relay.seed("pixel", "cid-s", "side", "Groceries", 1_700_000_000)
        relay.seedMessage("pixel", "cid-s", "mid-s", "user", "milk", 1_700_000_002)
        val p = phone(relay, "phone-a", sideChats = false)
        p.chats.addSession("main"); p.chats.main = "main"
        p.chats.addSession("side", "Trip")
        p.chats.user("main", "hi")
        p.chats.user("side", "plan a trip", at = 1_690_000_000_000L); p.chats.assistant("side", "where to?", at = 1_690_000_001_000L)
        p.engine.push()
        assertFalse(p.chats.sessions.values.any { it.title == "Groceries" }) // the other device's side chat is not here
        assertEquals(1, relay.convs.values.count { it.kind == "main" })
        assertNull(p.store.convs["side"])

        // the switch goes on
        p.sideChats = true
        p.engine.sideChatsTurnedOn()
        p.engine.pull()
        assertEquals(Triple(0L, SyncApi.SCOPE_ALL, SyncEngine.TAIL), relay.changeCalls.last())
        // the other device's side chat is here now, the main chat is unchanged (one row per mid)
        assertTrue(p.chats.sessions.values.any { it.title == "Groceries" })
        assertEquals(1, p.chats.messages("main").size)
        // and the backfill: this phone's side chat goes up, oldest first
        p.engine.push()
        val sideCid = p.store.convs.getValue("side").cid
        assertEquals("side", relay.convs.getValue(sideCid).kind)
        val sent = relay.msgs.values.filter { it.cid == sideCid }.sortedBy { it.seq }
        assertEquals(listOf("plan a trip", "where to?"), sent.map { it.text })

        // off again: the side chats rest, the main chat still moves
        p.sideChats = false
        p.chats.user("side", "more"); p.chats.user("main", "again")
        p.engine.push()
        assertTrue(relay.msgs.values.any { it.text == "again" })
        assertFalse(relay.msgs.values.any { it.text == "more" })
        relay.seedMessage("pixel", "cid-s", "mid-s2", "user", "eggs", 1_700_000_500)
        assertEquals(0, p.engine.pull().applied)
        assertEquals(SyncApi.SCOPE_MAIN, relay.changeCalls.last().second)
        assertEquals(1, p.chats.messages(p.chats.sessions.values.first { it.title == "Groceries" }.id).size)
    }

    @Test fun `C9 remote rows - the rows another device wrote are known by id, this phone's are not`() = runTest {
        val relay = FakeRelay(mapOf("desk-1" to "Mac"))
        relay.seed("desk-1", "cid-main", "main", null, 1_700_000_000)
        relay.seedMessage("desk-1", "cid-main", "mid-0", "user", "from the mac", 1_700_000_000)
        val p = phone(relay, "phone-a", sideChats = false)
        p.chats.addSession("main"); p.chats.main = "main"
        p.chats.user("main", "from the phone", at = 1_700_000_001_000)
        p.engine.push()

        val remote = p.engine.remoteRows()
        val rows = p.chats.messages("main")
        assertEquals(2, rows.size)
        val macRow = rows.first { Transcript.items(listOf(it)).first().text == "from the mac" }
        val phoneRow = rows.first { Transcript.items(listOf(it)).first().text == "from the phone" }
        assertEquals(mapOf(macRow.id to "desk-1"), remote)
        assertFalse(phoneRow.id in remote)
        // an echo of our own row under a lost mid stays ours
        relay.seedMessage("phone-a", "cid-main", "mid-lost", "user", "from the phone", 1_700_000_001)
        p.engine.pull()
        assertEquals(1, p.engine.remoteRows().size)
    }

    @Test fun `C9 working - presence goes out for a pushed chat in scope, never for one the relay does not know`() = runTest {
        val relay = FakeRelay()
        val p = phone(relay, "phone-a", sideChats = false)
        p.chats.addSession("main"); p.chats.main = "main"
        p.chats.addSession("side", "Trip")
        p.chats.user("main", "hi"); p.chats.user("side", "x")

        // before the first push nothing is mapped: nothing goes out
        assertFalse(p.engine.working("main", true))
        p.engine.push()
        assertTrue(p.engine.working("main", true))
        assertTrue(p.engine.working("main", false))
        // a side chat while the switch is off is not on the relay
        assertFalse(p.engine.working("side", true))
        val cid = p.store.convs.getValue("main").cid
        assertEquals(listOf(Triple("phone-a", cid, true), Triple("phone-a", cid, false)), relay.workingCalls)
        // what the relay reports is readable through state()
        relay.workingNow += WorkingPresence(cid, "desk-1", "Mac", 1_700_000_000L)
        assertEquals("desk-1", relay.state().working.single().from)
        assertTrue(relay.state().working.single().live(1_700_000_000L + 599))
        assertFalse(relay.state().working.single().live(1_700_000_000L + 600))
    }
}
