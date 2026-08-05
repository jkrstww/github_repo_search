# Message Track

`MessageTrack` persists every complete response emitted by an agent while it
works on a benchmark instance. It does not synthesize separate trajectory
events for tool calls, edits, or verification: those items remain inside the
raw response exactly as the agent provider returned them.

```python
from message_track import MessageTrack

track = MessageTrack(
    "instances/feature_implement/legado-Harmony-b5f43c23-21da-472f-b486-15304d658b18"
)

response = agent.run(task)
track.record_response(response, metadata={"turn": 1})
track.close(status="completed")
```

Dictionary responses are stored directly. SDK response objects are supported
when they expose `model_dump()` or `to_dict()`. This retains response IDs,
output messages, reasoning items, function/tool calls, model metadata, status,
and usage without imposing a provider-specific schema.

The default output is:

```text
instance_tracks/<instance-name>/trajectory.json
```

The document contains the instance metadata plus an ordered `responses` list.
Each entry adds only capture metadata (`seq`, `recorded_at`, `response_id`, and
caller-supplied `metadata`) around the complete raw `response`. Every append is
persisted using an atomic file replacement, and an unfinished track can be
resumed by constructing `MessageTrack` again for the same instance.
