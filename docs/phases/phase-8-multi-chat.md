# Phase 8 — Multi-chat Support

## Goal
Conversations are persisted in Postgres so the user can close the browser, come back, and resume any past conversation with full history intact. The sidebar shows a chronological list of all conversations with relative timestamps and a per-row delete button.

## Phase gate
Open `http://athena.local`, send a few messages, close the browser, reopen — conversation appears in the sidebar and is fully resumable. Create a second conversation and verify both appear ordered by most recent.

---

## What was built

### Postgres schema
Two new tables in `scripts/migrate.sql`:

```sql
CREATE TABLE conversations (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title      TEXT NOT NULL,              -- first 40 chars of the first user message
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,         -- "user" or "assistant"
    content         TEXT NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_messages_conversation_id ON messages(conversation_id);
```

### Agent API (`agent/main.py`)

**Updated `POST /chat`:**
- `ChatRequest` gains `conversation_id: str | None = None`
- If null: creates a new conversation with title = first 40 chars of message
- If set: loads full history from `messages` ordered by `created_at ASC` and passes it as the `messages` array to `create_react_agent` so the agent has full context
- After response: inserts both the user message and assistant response, bumps `updated_at` on the conversation
- Response now includes `conversation_id` so the frontend can track it across turns

**New endpoints:**
- `GET /conversations` — all conversations ordered by `updated_at DESC`
- `GET /conversations/{id}/messages` — all messages for a conversation ordered by `created_at ASC`
- `DELETE /conversations/{id}` — deletes conversation and cascades to messages

### Frontend

**`App.tsx`:** lifts `conversationId` state up; passes a `refreshRef` callback to the sidebar so ChatView can trigger a list refresh after each message.

**`Sidebar.tsx`:** fetches `GET /conversations` on mount and after every message; displays conversations under a "Recent" header with title and relative timestamp ("2h ago", "yesterday"); active conversation highlighted with indigo left-border; trash icon appears on hover and calls `DELETE /conversations/{id}`; "New conversation" clears active state.

**`ChatView.tsx`:** sends `conversation_id` (null on first message, the stored UUID on all subsequent messages in the session); stores the returned `conversation_id` from the first response; calls `onConversationUpdate()` to refresh the sidebar list after each turn.

**`nginx.conf`:** added `conversations` to the proxy regex so `/conversations` and `/conversations/:id/messages` route to the agent.

---

## Issues encountered

### Image imported on wrong node
The agent pod runs on **xdev-sr**, but the new image was imported on **vlinux2**. The pod restarted after a power outage and pulled the old cached image from xdev-sr, causing all new endpoints to return 404. Fix: always check `kubectl get pods -o wide` and import the image on the node where the pod is scheduled.

### Power outage mid-deploy
A full power cut took down all three nodes during the Phase 8 deploy. k3s and all pods recovered automatically on reboot (1 restart each). Postgres data survived intact. The pending schema migration had not yet been applied; it was run inline via `kubectl exec ... psql -c "..."` after recovery since the SQL file existed only on the dev machine.

### `/tmp` wiped on reboot
Docker-saved image tars in `/tmp` do not survive reboots. After the power outage, `/tmp/athena-agent.tar.gz` was gone from xdev-sr and had to be rebuilt.

### Unused `useRef` import
TypeScript strict mode (`tsc`) caught `useRef` imported but unused in `Sidebar.tsx`, breaking the Docker build. Removed the import.

---

## Build process

```bash
# On xdev-sr (agent runs here)
sudo docker build -t athena-agent:latest agent/
sudo docker save athena-agent:latest | gzip > /tmp/athena-agent.tar.gz
sudo chmod 644 /tmp/athena-agent.tar.gz
sudo k3s ctr images import /tmp/athena-agent.tar.gz

# On vlinux2 (frontend runs here) — pull from xdev-sr
scp ubuntu@192.168.96.201:/tmp/athena-frontend.tar.gz /tmp/
sudo k3s ctr images import /tmp/athena-frontend.tar.gz

# From vlinux1 or laptop
kubectl rollout restart deployment/agent -n athena
kubectl rollout restart deployment/frontend -n athena
```

---

## Next phase
Phase 4 (Rust MCP server) — deferred. Planned: expose internship tracker, LeetCode lookup, and Twilio SMS as MCP tools callable from the agent more cleanly than direct Python functions.
