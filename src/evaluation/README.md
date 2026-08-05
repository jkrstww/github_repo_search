# 轨迹评估

本模块用于评估 schema-v2 的 `message_track` 文件。评估过程是确定性的，
不读取隐藏思维链，也不使用 LLM judge。

## 1. 动作有效性

这个指标衡量的是：轨迹里记录的工具调用，是否真的执行成功了。

评估器按下面的顺序判断一个动作是否成功：

1. `success`：`true` 视为成功，`false` 视为失败；
2. `is_error`：如果为真，则失败；否则成功；
3. `exit_code`：`0` 视为成功，非 `0` 视为失败；
4. `status`：`completed`、`success`、`succeeded`、`ok`、`passed` 视为成功；
   `failed`、`error`、`cancelled`、`canceled`、`timeout` 视为失败；
5. `error`：只要该字段非空，就视为失败。

如果一个动作没有任何可判断成功/失败的信号，它的结果就是未知，
不会计入成功率分母。对于同一个 `action_id` 的开始/结束记录，
评估器会合并为一个动作；例如 Codex 的 `command_execution` 起止记录会被合并。

输出包含以下内容：

- 总动作数；
- 已知结果动作数；
- 未知结果动作数；
- 成功动作数；
- 失败动作数；
- 覆盖率；
- 工具执行成功率。

其中：

```text
tool_execution_success_rate = successful_actions / known_outcome_actions
```

这个指标只表示“工具是否执行成功”，不表示这个动作是否真的有助于完成任务。

## 2. 执行轮次

一个持久化的 agent response 就是一轮。

评估器统计：

- 总响应轮次；
- 包含工具动作的响应轮次；
- 不包含工具动作的响应轮次；
- 第一条和最后一条响应的序号。

同一轮里输出多少内容、包含多少 item，都不会拆成额外轮次。

## 3. 跨文件检索

这个指标衡量的是：agent 在轨迹里有没有提到实例要求关注的关键文件。

期望文件来自 instance 元数据，包括：

- 抽象定义文件 `abstract_node.path`；
- 被 mask 的目标文件 `mask.path`；
- `affected_modules`；
- `reference_implementation_files`。

只要 agent response 里出现了这些仓库相对路径，评估器就认为该文件被检索到。
主指标为：

```text
file_recall = retrieved_expected_files / all_expected_files
```

另外还单独报告：

- 关键文件召回率：抽象定义文件 + 被 mask 的目标文件；
- 参考实现文件召回率：`reference_implementation_files` 的召回率。

额外提到的文件不会被扣分，因为实例元数据本身并不是所有可用文件的完整列表。

## CLI

```bash
PYTHONPATH=src python -m evaluation \
  instance_tracks/<instance-name>/trajectory.json \
  --output instance_tracks/<instance-name>/evaluation.json
```

如果 `responses` 为空，依赖响应的字段会返回 `insufficient_data` 和 `null`，
不会伪装成 0 分。
