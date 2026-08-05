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

When you do not have a raw SDK response, use `record_turn()` together with
`build_tool_action()` to persist a provider-neutral agent turn. This keeps the
trajectory compatible with `src/evaluation`, which expects tool actions and
file mentions to live inside each stored response.

```python
from message_track import MessageTrack, build_tool_action

track = MessageTrack("instances/feature_implement/<instance-id>")
track.record_turn(
    message="查看抽象定义并补全被 mask 的文件",
    actions=[
        build_tool_action(
            action_type="command_execution",
            action_id="cmd_1",
            command="sed -n '1,120p' entry/src/main/ets/Foo.ets",
            status="completed",
            exit_code=0,
            output="...",
        )
    ],
    file_paths=["entry/src/main/ets/Foo.ets"],
    metadata={"turn": 1},
)
track.close(status="completed")
```

The default output is:

```text
instance_tracks/<instance-name>/trajectory.json
```

The document contains the instance metadata plus an ordered `responses` list.
Each entry adds only capture metadata (`seq`, `recorded_at`, `response_id`, and
caller-supplied `metadata`) around the complete raw `response`. Every append is
persisted using an atomic file replacement, and an unfinished track can be
resumed by constructing `MessageTrack` again for the same instance.

## Evaluation

`trajectory.json` is evaluated by `src/evaluation`. The evaluator is
deterministic and does not use an LLM judge. It reads the persisted `responses`
and instance metadata, then reports three metrics:

1. `action_validity` — whether observed tool executions succeeded, using
   explicit fields such as `success`, `is_error`, `exit_code`, `status`, and
   `error`.
2. `execution_rounds` — how many persisted agent responses exist, and how many
   of them contain tool actions.
3. `cross_file_retrieval` — whether the agent mentioned benchmark-known file
   paths from the abstract node, masked target, reference implementation files,
   and affected modules.

The evaluator only checks the recorded track content. It does not inspect
hidden reasoning, and it does not judge whether a tool action was useful beyond
its execution outcome.
