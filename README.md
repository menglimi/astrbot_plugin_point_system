# 群积分助手 (astrbot_plugin_point_system)

## 插件简介
`astrbot_plugin_point_system` 是一个面向 AstrBot 群聊场景的积分互动插件，围绕“签到、活跃、抽奖、兑换、管理”这几类高频玩法设计。它支持按群维护成员信息、自动保存数据、定时备份、日期口令奖励，以及负分限制和群头衔联动，适合做群活跃体系或轻量积分经济。

**版本：2.4.1**

**展示名称：** `群积分助手`  
**GitHub 仓库：** [https://github.com/menglimi/astrbot_plugin_point_system](https://github.com/menglimi/astrbot_plugin_point_system)

---

## 支持平台
- AstrBot 插件环境
- QQ / AIOCQHTTP：功能最完整，支持头衔、设精、禁言、群头衔同步等能力
- 其他消息平台：可使用基础积分、签到、抽奖、排行等通用功能，但平台专属管理能力可能不可用

## 安装方式
1. 将插件目录放入 AstrBot 的插件目录，例如 `<AstrBot 数据目录>/plugins/astrbot_plugin_point_system`
2. 重启 AstrBot 或在插件管理中重新加载插件
3. 在 AstrBot 管理面板中根据 `_conf_schema.json` 调整配置
4. 首次运行后会自动在插件数据目录下生成积分数据文件
5. 插件目录附带了最小 [requirements.txt](requirements.txt) 说明文件；插件本身没有额外第三方 pip 依赖，运行依赖 AstrBot 主程序环境

---

## 首次使用
不需要一次配置完所有功能，建议先按下面的顺序确认基础流程：

1. 重载插件后，在群里发送 `/群聊签到`，再发送 `/我的积分`，确认签到和积分查询正常
2. 在插件配置中按需修改 `points_name`；不确定时保持默认的“积分”即可
3. 如需手动给测试用户补积分，将自己的用户 ID 加入 `admin_settings.points_admin_ids`，保存后发送 `/给积分 @用户 100`
4. 需要发红包时，先确认 `red_packet_settings` 保持开启；需要兑换功能时，再按下一节添加第一个兑换物
5. 抽奖、生日、日期口令和平台专属兑换均可之后逐项调整

### 5 分钟跑通第一个兑换物
管理员操作路径：`AstrBot 管理面板 → 插件 → 群积分助手 → 兑换管理`。点击“新增兑换物”，填写后保存即可。也可以在普通插件配置页编辑 `exchange_items`。

最小示例（示例名称和内容都可以替换）：

```json
{
  "exchange_items": [
    {
      "name": "群福利",
      "enabled": true,
      "cost": 100,
      "contents": [
        "奖品领取凭证-001",
        "奖品领取凭证-002"
      ],
      "private_only": true
    }
  ]
}
```

- `name` 是用户看到和输入的兑换物名称，可自由命名；名称应便于区分
- `contents` 中每一项代表一份库存，默认成功发放后不会再次发放；可填写兑换码、领取凭证、链接、口令或其他文本，也可使用 `image:https://...` 或 `video:https://...` 发送图片/视频
- `repeatable` 开启后不消耗库存，用户每次兑换都会再次获得同一份内容，适合长期有效的链接、资料或权益
- `selection_mode` 可填 `sequential`（按内容列表顺序）或 `random`（从可用内容中随机抽取）；随机模式关闭 `repeatable` 时，抽中的内容兑换后销毁
- `private_only` 默认建议开启；用户仍在群里发起兑换，Bot 会把完整兑换结果私聊发送，发放内容可以公开时可关闭
- `success_template` 可以直接保留默认值，也可以按需修改兑换成功提示
- `contents` 为空时会显示库存为 0，用户无法兑换，但不会扣除积分
- 如需限制兑换范围，可配置 `exchange_scope`；默认是黑名单模式且列表为空，表示不限制
- 切换为白名单模式后，只允许列表中命中的群或账号兑换；列表可填写群号、QQ 号，或使用 `group:群号` / `user:QQ号`

保存后，用户的完整操作流程是：

1. 在群里发送 `/兑换列表` 查看名称、价格、库存和发放方式
2. 选择兑换物后在群里发送 `/兑换 群福利`；也支持输入能够唯一匹配该名称的片段
3. 开启“通过私聊发放结果”时，Bot 会把完整兑换结果私聊发送，群里只显示不含发放内容的成功或状态提示
4. 若平台要求先建立私聊会话，用户先向 Bot 发送一条私聊消息即可回群重试；平台明确拒绝时插件会退还积分并释放库存
5. 若平台超时或无法确认是否送达，插件会保留积分与库存占用以防重复发放，并在兑换记录中标记“待核对”
6. 管理员补充新的 `contents` 项即可增加库存

---

## 核心功能
- 每日签到：支持固定积分或随机积分，支持首次签到奖励、每日首签奖励、连签奖励和每 7 天奖励
- 稀有彩蛋：签到时可低概率触发欧皇 / 非酋事件，自动加分或扣分
- 活跃奖励：群成员发送合规普通消息时，可静默获得积分
- 无前缀触发：支持“关键词+签到 / 签到+关键词”以及“关键词+抽奖 / 抽奖+关键词”
- 群内排行：优先展示当前群积分榜，群数据不足时回退全局排行
- 抽奖玩法：支持个人抽奖和群体抽奖，两种模式可独立开关
- 兑换玩法：支持自定义兑换物及积分价格，也支持兑换群头衔、设精消息和禁言
- 管理指令：支持独立管理员名单，允许通过 `@用户` 或直接输入 QQ 号增减积分
- 积分红包：管理员可发送固定、拼手气或口令红包，成员在群内领取
- 日期口令奖励：支持按日期、关键词、范围、概率发放奖励
- 生日系统：支持记录生日、生日签到奖励，以及按配置时间自动播报当日寿星名单
- 自动备份：支持多备份目标和每日定时备份
- 负分联动：负分用户仅可签到恢复积分，不能抽奖，并尽力同步 `群女仆X号` 头衔

---

## 积分规则概览

### 获取积分
- 每日签到
- 每日首签额外奖励，按每天 `04:00` 刷新
- 首次签到额外奖励
- 连续签到加成与每 7 天节点奖励
- 群聊活跃奖励
- 日期口令奖励
- 生日签到奖励
- 定时生日名单播报
- 抽奖中奖返利

### 消耗积分
- 个人抽奖
- 群体抽奖报名
- 兑换头衔
- 兑换设精
- 兑换禁言
- 自定义兑换物
- 管理员手动扣分

### 负分规则
- 允许积分进入负数
- 负分用户只能通过每日签到恢复积分
- 负分用户无法参与抽奖
- 负分用户不会获得活跃奖励和日期口令奖励
- 在 QQ / AIOCQHTTP 群聊中，负分用户会尽力同步 `群女仆X号` 头衔，转正后自动移除；若 Bot 没有群主权限，头衔同步会跳过并延迟重试，不影响每日签到恢复积分

---

## 插件流程
1. 插件启动时加载配置与本地积分数据，并自动兼容旧版数据结构
2. 群消息进入后，会先判断是否命中无前缀签到或无前缀抽奖口令
3. 如果是普通消息，则继续判断日期口令奖励和活跃奖励条件
4. 指令类消息由 `@filter.command` 处理签到、积分查询、排行榜、抽奖、兑换和管理操作
5. 所有积分变更都会在锁内更新并原子写入 `points_data.json`
6. 若启用自动备份，插件会在设定时间将数据文件备份到一个或多个目标地址

---

## 使用方法

### 普通用户指令
| 指令 | 说明 | 示例 |
| :--- | :--- | :--- |
| `/群聊签到` | 进行每日签到 | `/群聊签到` |
| `/补签` | 消耗积分补上最近一个漏签日，不重复发放签到奖励 | `/补签` |
| `/我的积分` | 查询自己的当前积分 | `/我的积分` |
| `/积分榜` | 查看当前群积分排行 | `/积分榜` |
| `/积分规则` | 查看当前积分获取规则 | `/积分规则` |
| `/抽奖` | 按默认模式参与抽奖 | `/抽奖` |
| `/抽奖 个人` | 进行个人抽奖 | `/抽奖 个人` |
| `/抽奖 群体` | 参与群体抽奖报名 | `/抽奖 群体` |
| `/兑换列表` | 查看自定义兑换物、价格和剩余库存 | `/兑换列表` |
| `/兑换 兑换物名称` | 兑换管理员配置的兑换物 | `/兑换 群福利` |
| `/兑换头衔 头衔内容` | 兑换群头衔 | `/兑换头衔 肝帝` |
| `/兑换设精` | 引用消息后兑换设精 | `回复一条消息后发送 /兑换设精` |
| `/兑换禁言` | 兑换自禁言 | `/兑换禁言` |
| `/兑换禁言 @某用户` | 兑换禁言他人 | `需先开启 allow_mute_others` |
| `/偷积分 @某用户` | 按配置概率尝试偷取指定用户的积分 | `需先开启 steal_settings.enabled` |
| `/抢红包` | 领取当前群最新的积分红包 | `/抢红包` |
| `/抢红包 口令` | 领取当前群最新的口令红包 | `/抢红包 春日快乐` |
| `/抢红包 编号 [口令]` | 按编号精确领取，兼容多个红包同时存在 | `/抢红包 A1B2C3D4 春日快乐` |
| `/记录生日 10/24` | 记录自己的生日 | `/记录生日 10/24` |
| `/生日签到` | 领取年度生日祝福与生日奖励；未记录时可按配置自动记录为今天 | `/生日签到` |

### 无前缀口令
签到和抽奖关键词都可在配置中单独修改。

| 口令 | 说明 |
| :--- | :--- |
| `<签到关键词>签到` / `签到<签到关键词>` | 无前缀签到 |
| `<签到关键词>补签` / `补签<签到关键词>` | 无前缀补签 |
| `<抽奖关键词>抽奖` / `抽奖<抽奖关键词>` | 无前缀抽奖，直接走当前默认抽奖模式 |
| `<生日签到触发词>` | 无前缀生日签到，效果同 `/生日签到` |

### 管理员指令
| 指令 | 说明 | 示例 |
| :--- | :--- | :--- |
| `/给积分 @用户 100` | 为目标用户增加积分 | `/给积分 @某用户 100` |
| `/给积分 123456789 100` | 通过 QQ 号为目标用户增加积分 | `/给积分 123456789 100` |
| `/扣积分 @用户 50` | 扣除目标用户积分 | `/扣积分 @某用户 50` |
| `/扣积分 123456789 50` | 通过 QQ 号扣除目标用户积分 | `/扣积分 123456789 50` |
| `/积分红包 固定 20 5` | 创建 5 份、每份 20 积分的红包（`/发红包` 也是别名） | `/积分红包 固定 20 5` |
| `/积分红包 拼手气 100 5` | 创建总额 100、共 5 份的拼手气红包 | `/积分红包 拼手气 100 5` |
| `/积分红包 口令 100 5 春日快乐` | 创建需要口令的红包 | `/积分红包 口令 100 5 春日快乐` |
| `/清空所有数据 确认` | 清空全部积分、抽奖、红包、生日与群记录 | `/清空所有数据 确认` |

管理员权限由 `admin_settings.points_admin_ids` 控制，只有配置过的 QQ 号可以增减积分或创建红包。红包由系统发放，不扣管理员个人积分。

---

## 配置说明
插件通过 `_conf_schema.json` 暴露配置项，下面按功能分组说明常用配置。

### 基础配置
- `points_name`：积分名称，可改为金币、贡献度等
- `message_templates.*`：消息模板配置
- `leaderboard_settings.display_limit`：排行榜显示数量
- `leaderboard_settings.show_self_rank`：是否显示自己的名次

### 签到配置
- `sign_in_settings.sign_in_mode`：`random` 或 `fixed`
- `sign_in_settings.fixed_sign_in_points`：固定签到积分
- `sign_in_settings.min_sign_in_points` / `max_sign_in_points`：随机签到范围
- `sign_in_settings.first_sign_in_bonus`：首次签到奖励
- `sign_in_settings.daily_first_sign_in_bonus`：每日首签额外奖励
- `sign_in_settings.streak_bonus_enabled`：是否启用连签奖励
- `sign_in_settings.streak_step_bonus`：连签每日递增奖励
- `sign_in_settings.streak_bonus_cap`：连签奖励上限
- `sign_in_settings.weekly_streak_bonus`：每连续 7 天额外奖励
- `sign_in_settings.make_up_cost`：补签每次消耗的积分，填 `0` 表示免费
- `sign_in_settings.make_up_monthly_limit`：每个用户每月补签次数上限，填 `0` 表示不限次数
- `sign_in_settings.fortune_event_enabled`：是否开启欧皇 / 非酋彩蛋
- `sign_in_settings.fortune_event_chance`：彩蛋触发概率
- `sign_in_settings.fortune_event_points`：彩蛋积分变化值
- `sign_in_settings.fortune_pity_enabled`：是否开启彩蛋保底
- `sign_in_settings.fortune_lucky_pity_threshold`：欧皇保底次数
- `sign_in_settings.fortune_unlucky_pity_threshold`：非酋保底次数

### 无前缀触发配置
- `sign_in_trigger`：旧版兼容用的完整签到口令
- `sign_in_trigger_keyword`：签到口令关键词，支持“关键词+签到”和“签到+关键词”
- `lottery_trigger`：旧版兼容用的完整抽奖口令，也可直接填写要匹配的完整短语
- `lottery_trigger_keyword`：抽奖口令关键词，支持“关键词+抽奖”和“抽奖+关键词”

### 生日配置
- `birthday_settings.enabled`：是否开启生日功能
- `birthday_settings.sign_in_trigger`：生日签到触发词
- `birthday_settings.reward_points`：生日签到奖励积分
- `birthday_settings.auto_record_when_unset`：未记录生日时是否自动记为当天
- `birthday_settings.auto_broadcast_enabled`：是否开启寿星名单定时播报
- `birthday_settings.auto_broadcast_time`：寿星名单播报时间，格式为 `HH:MM`

### 活跃奖励配置
- `activity_settings.enabled`：是否开启活跃奖励
- `activity_settings.points_per_message`：每次奖励积分
- `activity_settings.cooldown_seconds`：冷却时间
- `activity_settings.daily_limit`：每日奖励次数上限
- `activity_settings.min_text_length`：最短消息长度限制

### 偷积分配置
- `steal_settings.enabled`：是否开启偷积分功能，默认关闭
- `steal_settings.daily_steal_limit`：每人每日发起偷积分次数，填 `0` 表示不限
- `steal_settings.daily_be_stolen_limit`：每人每日计入被偷次数，填 `0` 表示不限
- `steal_settings.failure_counts_as_stolen`：偷取失败是否计入被偷次数，默认关闭
- `steal_settings.min_points` / `max_points`：成功时随机偷取的积分范围，实际不会超过被偷者余额
- `steal_settings.success_probability`：成功概率，填写 `0` 到 `1` 的小数
- `steal_settings.failure_cost`：失败时扣除发起者的积分，填 `0` 表示不扣
- `steal_settings.failure_cost_to_victim`：失败扣除是否转给被偷者

### 抽奖配置
- `lottery_settings.enabled`：总开关
- `lottery_settings.default_mode`：默认抽奖模式，支持 `personal` 和 `group`
- `lottery_settings.personal_enabled` / `lottery_settings.group_enabled`：个人 / 群体抽奖开关
- `lottery_settings.personal_cost`：个人抽奖消耗积分
- `lottery_settings.personal_daily_limit`：个人抽奖每日次数
- `lottery_settings.personal_prizes.*`：个人抽奖五档奖项与概率权重
- `lottery_settings.group_cost`：群体抽奖报名积分
- `lottery_settings.group_daily_limit_per_user`：群体抽奖每人每日参与上限
- `lottery_settings.group_required_participants`：群体抽奖开奖人数
- `lottery_settings.group_distribution_ratios`：群体奖池分配比例

### 兑换配置
- `exchange_items`：自定义兑换物列表；每项可设置 `name`、`enabled`、`cost`、`content_type`、`contents`、`selection_mode`、`repeatable`、`private_only` 和 `success_template`
- `exchange_items.*.content_type`：`text` 文本、`image` 图片或 `video` 视频；管理页会先选择类型，再显示对应的内容管理界面
- `exchange_items.*.name`：用户可见且用于 `/兑换 名称` 的自定义兑换物名称；支持完整名称或唯一片段匹配
- `exchange_items.*.enabled`：控制该兑换物是否出现在列表中；可先关闭并编辑草稿，准备好后再开启
- `exchange_items.*.cost`：每次兑换扣除的积分
- `exchange_items.*.contents`：待发放内容列表；普通文本按文字发送，使用 `image:` / `video:` 前缀可发送图片或视频
- `exchange_items.*.repeatable`：是否允许重复兑换；默认关闭，开启后不消耗内容库存
- `exchange_items.*.selection_mode`：`sequential` 按序发放，`random` 随机发放
- `exchange_items.*.private_only`：是否把兑换结果主动私聊给群内发起兑换的用户；关闭后会在发起会话直接发送
- `exchange_items.*.success_template`：成功提示，支持 `{item}`、`{content}`、`{cost}`、`{points_name}`、`{remaining}`
- `exchange_scope.mode`：自定义兑换物范围模式，`blacklist`（默认）排除列表中的群或账号，`whitelist` 只允许列表中的群或账号
- `exchange_scope.scope`：范围列表，每项填写群号、QQ 号或 `group:` / `user:` 前缀；黑名单留空表示不限制，白名单留空时不会向群开放
- `exchange_settings.title_enabled` / `title_cost` / `title_max_length`
- `exchange_settings.essence_enabled` / `essence_cost`
- `exchange_settings.mute_enabled` / `mute_cost` / `mute_duration_seconds`
- `exchange_settings.allow_mute_others`

### 兑换管理拓展页
- 入口为 `AstrBot 管理面板 → 插件 → 群积分助手 → 兑换管理`
- “总览”支持按群号搜索并切换群聊，核心指标、排行、分布和趋势会同步按群成员重算
- 总积分折线支持最近 24 小时、7 天和 30 天；真实快照从 2.3.0 启用后开始采集，不会伪造升级前的历史
- 新增兑换物后依次填写名称、所需积分和发放内容；发放内容支持每行一份批量粘贴，最后点击保存
- 页面可直接切换黑名单 / 白名单并维护适用群或账号；白名单为空时会提示当前未开放兑换，但不会阻止保存
- 页面提供可用库存、启用项、累计发放和积分消耗概览
- 兑换记录展示兑换物、用户 ID、消耗、时间与发放状态；发放内容不会被复制到兑换记录中
- 页面保存带配置修订校验，遇到其他页面已更新时会要求刷新，避免静默覆盖

### 管理配置
- `admin_settings.points_admin_ids`：积分管理员 QQ 列表
- `admin_settings.log_operations`：是否记录管理员操作日志
- `admin_settings.max_admin_give`：单次管理操作允许的最大加分值

### 积分红包配置
- `red_packet_settings.enabled`：是否允许管理员创建新红包
- `red_packet_settings.max_total_points`：单个红包的积分上限，默认 `100000`
- `red_packet_settings.max_count`：单个红包的最多份数，默认 `100`
- `red_packet_settings.expire_minutes`：红包有效时长，默认 `1440` 分钟；填 `0` 表示不过期
- 固定红包的格式为“每份积分 + 份数”；拼手气和口令红包的格式为“总积分 + 份数”
- 口令只保存哈希，不会写入红包记录或日志；红包只能在创建它的群里领取，每人每个红包只能领一次

### 日期口令奖励配置
- `special_date_reward_entries`：词条列表
- 单条词条支持 `name`、`enabled`、`priority`、`scope`、`dates`、`keywords`
- 单条词条支持 `reward_points`、`daily_limit_per_user`、`probability`
- 单条词条支持 `announce`、`reply_template`、`exact_match`
- `keywords` 默认按普通文本包含匹配；如需正则，请显式使用 `re:` 前缀，并仅使用简短的安全表达式

### 备份配置
- `backup_settings.enabled`：是否开启自动备份
- `backup_settings.backup_paths`：备份目标列表，支持目录和文件路径
- `backup_settings.auto_backup_time`：自动备份时间，格式为 `HH:MM`

### 负分提示配置
- `negative_settings.debt_message`：负分状态下尝试抽奖时显示的提示文案

---

## 数据文件
- 主数据文件：`<AstrBot数据目录>\plugin_data\astrbot_plugin_point_system\points_data.json`
- 红包记录与领取状态和积分数据一起保存，插件升级时会自动迁移旧数据
- 备份文件：按配置写入 `backup_settings.backup_paths`
- 数据写入方式：锁保护 + 原子替换，减少异常退出时的损坏风险

---

## 注意事项
1. 兑换头衔、兑换设精、兑换禁言、负分头衔同步依赖 QQ / AIOCQHTTP 能力；负分头衔还通常需要 Bot 具备群主权限，权限不足时只跳过头衔展示，不影响积分功能
2. 机器人若没有对应群管理权限，兑换操作会失败；涉及先扣后调接口的场景会自动退款
3. 群内私聊发放在 QQ / AIOCQHTTP 上可直接调用私聊接口；其他平台需要用户先与 Bot 建立一次真实私聊会话，插件记录该会话后才能从群里发放
4. 群体抽奖若当天未凑齐开奖人数，会在次日首次触发群体抽奖时自动退款
5. 无前缀抽奖会直接使用当前配置中的默认抽奖模式
6. 负分用户无法参与抽奖，也不会再获得活跃奖励或日期口令奖励
7. 备份地址填写目录时会自动生成时间戳文件，填写文件路径时会在文件名后追加时间戳
8. 请勿手动破坏 `points_data.json` 的编码和结构，插件默认使用 UTF-8 读写

---

## 开发验证
发布前建议至少完成以下检查：

- 编译检查：`python -m py_compile main.py page_api.py birthday_feature.py lottery_feature.py`
- 页面脚本检查：`node --check pages/兑换管理/app.js`
- `_conf_schema.json` JSON 解析与全部文本文件 UTF-8 编码检查
- 兑换命令、配置保存冲突、桌面与移动端页面回归

---

## 更新记录

### 2.4.1
- 优化群聊活跃积分的高频读写，合并延迟保存请求，减少重复重写完整数据文件造成的卡顿
- 使用紧凑 UTF-8 JSON 持久化格式，增加即时保存与延迟保存的并发校验，避免旧快照覆盖新数据

### 2.4.0
- 新增可选偷积分玩法：支持每日发起次数、每日被偷次数、随机偷取范围、成功概率、失败扣除及失败积分转让配置
- 新增 `/偷积分 @用户` 和 `/偷积分 QQ号` 命令，数据迁移版本升级至 13

### 2.3.1
- 兼容命令末尾附加的 `[MSG_ID:数字]` 消息标记，修复 AstrBot 4.23.6 环境下 `/给积分` 与 `/兑换` 参数解析失败的问题
- 补充兑换名称、手动给积分及连续消息标记的命令解析回归测试

### 2.3.0
- 总览新增真实总积分快照与折线图，支持最近 24 小时、7 天和 30 天范围
- 新增可搜索的群聊筛选，核心指标、今日动态、积分分布、排行和趋势可按群查看
- 快照按 15 分钟分桶并保留 90 天；兑换流水开始记录群号，旧流水继续按群成员归属兼容筛选

### 2.1.0
- 新增管理员积分红包，支持固定、拼手气和口令三种模式
- 新增 `/抢红包`、`/领红包` 和 `/红包`，直接发送命令即可领取当前群最新红包；红包按群隔离、每人每包限领一次并支持过期时间
- 红包领取在数据锁内完成，保存失败会自动回滚，避免重复领取或超发
- 拼手气红包最后一份领取成功时会公布手气王及其领取金额；旧版本缺少完整领取明细时不会强行推测
- 新增 `red_packet_settings` 配置，可调整开关、单包上限、份数上限和有效时长

### 2.0.1
- 修复群内兑换的私聊发放流程：用户仍在群里发送兑换命令，Bot 会将完整结果发送到真实私聊会话，群内只显示不含兑换内容的结果提示
- 明确私聊投递状态：平台明确拒绝时自动退还积分并释放库存，超时或状态不明时保留占用并标记“待核对”，避免重复发放
- 增强跨平台私聊路由识别、真实私聊会话记录和清空数据后的兑换代际保护
- 修复自定义成功模板中的未知占位符导致兑换异常的问题，并避免日志记录兑换内容
- 拓展页兑换记录新增“已发放 / 待核对”状态显示

### 2.0.0
- 新增自定义兑换物适用范围，可按群号或 QQ 号配置黑名单 / 白名单
- 默认黑名单模式且范围留空，保持所有群和账号可兑换；拓展页提供范围切换和即时提示

### 1.9.0
- 新增通用自定义兑换物，可分别配置名称、积分价格、发放内容和成功提示
- 新增 `/兑换列表` 与 `/兑换 兑换物名称`，支持唯一模糊匹配兑换物名称
- 发放内容通过持久化指纹防止重复领取，并在同一事务中完成库存占用与积分扣除
- 支持用户在群内发起兑换，并按兑换物选择私聊发放结果或在当前会话公开发放
- 新增“兑换管理”拓展页，支持库存编辑、运行概览、兑换记录、明暗主题和手机布局

### 1.8.6
- 修复用户 ID 归一化不一致导致的脏键残留问题
- 修复引用消息提取在遇到无效 `Reply` 组件时提前返回的问题
- 修复管理员改分污染当前群成员表的问题
- 将日期词条关键词改为默认文本匹配，`re:` 前缀才启用受限正则，降低 ReDoS 风险
- 优化生日播报调度，避免同一时段重复触发
- 修复群体抽奖在人数阈值变更时可能出现的奖池分配截断问题，并补充抽奖审计日志

### 1.8.5
- 新增最小 `requirements.txt`
- 明确插件本身无额外第三方 pip 依赖，运行依赖 AstrBot 主程序环境

### 1.8.4
- 新增积分管理员指令 `/清空所有数据 确认`
- 清空时会重置全部积分、抽奖、生日和群记录
- 在 QQ / AIOCQHTTP 环境下会尽量先移除已同步的负分头衔

### 1.8.3
- 修复非酋事件扣分仍被限制为最低 0 的问题，现在可以正确进入负分
- 将生日相关逻辑拆分到 `birthday_feature.py`
- 将抽奖相关逻辑拆分到 `lottery_feature.py`
- 同步修正作者与仓库地址信息

### 1.8.2
- 简化签到成功、重复签到和积分查询等高频提示
- 统一所有普通返回消息为单句输出，避免分段刷屏

### 1.8.1
- 去除生日功能和负分提示中的硬编码文案，改为配置驱动
- 文档中的无前缀触发词、生日奖励和播报时间改为通用占位说明
- 移除写死的 `星缘积分规则` 别名，避免品牌绑定

### 1.8.0
- 新增 `/记录生日 mm/dd`，可手动记录生日
- 生日当天使用普通签到也会自动触发生日签到奖励
- 未记录生日时使用 `/生日签到` 或 `生日签到` 会自动将今天记为生日
- 每天按配置时间自动检查群内寿星并发送名单，没有寿星则不播报

### 1.7.2
- 新增 `生日签到`，每位用户每年可领取一次生日祝福与生日奖励
- 负分状态下的抽奖拦截提示调整为债务 / 女仆装风格文案

### 1.7.1
- 新增无前缀抽奖
- 签到和抽奖都支持“关键词在前 / 关键词在后”两种口令格式
- 无前缀关键词改为可配置

### 1.7.0
- 支持负分
- 负分用户限制为仅可签到恢复积分
- 尽力同步 `群女仆X号` 头衔，权限不足时不影响负分用户签到恢复积分

### 1.6.x
- 增加每日首签奖励和 `04:00` 刷新
- 增加欧皇 / 非酋彩蛋与保底
- 增加自动备份
- 支持固定签到或随机签到

### 1.5.0
- 增加个人抽奖和群体抽奖
- 增加日期口令奖励词条

### 1.2.0
- 增加兑换头衔、设精、禁言

### 1.1.0
- 优化排行榜、存储和旧数据迁移

---

## 开发信息
1. 开发者：`menglimi`
2. 插件标识：`astrbot_plugin_point_system`
3. 展示名称：`群积分助手`
4. 仓库地址：[https://github.com/menglimi/astrbot_plugin_point_system](https://github.com/menglimi/astrbot_plugin_point_system)
5. 数据目录：`<AstrBot数据目录>\plugin_data\astrbot_plugin_point_system`
6. 最低 AstrBot 版本：`4.22.0`

扩展页内置 Lucide `v0.468.0` 图标库，按 [ISC License](pages/兑换管理/LICENSE) 使用。
